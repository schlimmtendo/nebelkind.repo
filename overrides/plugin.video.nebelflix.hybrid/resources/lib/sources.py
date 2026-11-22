

# edit 2026-04-08

import sys
import xbmcgui
import re,json,random,time,threading,traceback
from resources.lib.search_workers import SearchExecutor
from resources.lib.source_schedule import fair_provider_order
from resources.lib import load_timing
from resources.lib import log_utils, control
from resources.lib.utils import get_titles
from resources.lib.control import py2_decode, py2_encode, quote_plus, parse_qsl
import resolveurl as resolver
# from functools import reduce
from resources.lib.control import getKodiVersion

if int(getKodiVersion()) >= 20: from resources.lib.listitem import ListItemInfoTag

# für self.sysmeta - zur späteren verwendung als meta
_params = dict(parse_qsl(sys.argv[2].replace('?',''))) if len(sys.argv) > 1 else dict()


class _NextProgress:
    """Small Kodi notification while the next episode's sources are resolved."""
    def __init__(self):
        self.dialog = None

    def create(self, *args, **kwargs):
        self.dialog = xbmcgui.DialogProgressBG()
        self.dialog.create('Nebelflix · Nächste Folge', 'Suche Abspielquelle …')

    def update(self, percent, message=''):
        if self.dialog is not None:
            try:
                self.dialog.update(max(0, min(100, int(percent))),
                                   'Nebelflix · Nächste Folge', str(message)[:180])
            except Exception:
                pass

    def close(self):
        if self.dialog is not None:
            try:
                self.dialog.close()
            except Exception:
                pass
            self.dialog = None

    def iscanceled(self):
        return False


class _SilentProgress:
    def create(self, *args, **kwargs): pass
    def update(self, *args, **kwargs): pass
    def close(self, *args, **kwargs): pass
    def iscanceled(self): return False


def _bold(text):
    # Alias auf den zentralen Helper (control.bold) -- selbe Toggle-Logik fuer alle Dateien.
    return control.bold(text)


class sources:
    def __init__(self):
        self.getConstants()
        self.sources = []
        self.current = int(time.time())
        if 'sysmeta' in _params: self.sysmeta = _params['sysmeta'] # string zur späteren verwendung als meta
        self.watcher = False
        self.executor = SearchExecutor(max_workers=3 if control.condVisibility('System.Platform.Android') else 6)
        self.resolve_executor = SearchExecutor(max_workers=2)
        self._cancelled = threading.Event()
        self._collecting = True
        self.url = None
        self._series_next_autoplay = False
        self.scraper_stats = {}
        self.scraper_stats_lock = threading.Lock()

    def get(self, params):
        self.sysmeta = params['sysmeta']
        data = json.loads(self.sysmeta)
        self.mediatype = data.get('mediatype')
        self.aliases = data.get('aliases') if 'aliases' in data else []
        self._series_next_autoplay = bool(data.get('_series_next_autoplay')) and data.get('mediatype') != 'movie'

        title = py2_encode(data.get('title'))
        originaltitle = py2_encode(data.get('originaltitle')) if 'originaltitle' in data else title
        year = data.get('year') if 'year' in data else None
        imdb = data.get('imdb_id') if 'imdb_id' in data else data.get('imdbnumber') if 'imdbnumber' in data else None
        if not imdb and 'imdb' in data: imdb = data.get('imdb')
        tmdb = data.get('tmdb_id') if 'tmdb_id' in data else None
        self._mscout_tmdb_id = str(tmdb or '')
        #if tmdb and not imdb: print 'hallo' #TODO
        season = data.get('season') if 'season' in data else 0
        episode = data.get('episode') if 'episode' in data else 0
        premiered = data.get('premiered') if 'premiered' in data else None
        meta = params['sysmeta']
        select = data.get('select') if 'select' in data else None
        return title, year, imdb, season, episode, originaltitle, premiered, meta, select

    def play(self, params):
        title, year, imdb, season, episode, originaltitle, premiered, meta, select = self.get(params)
        try:
            url = None
            # Select playback mode BEFORE scraping so we can keep the entire
            # search + media-probe progress in the background for autoplay.
            if select is None:
                override_key = 'series.play.mode' if season not in (0, '0', '', None) else 'movie.play.mode'
                override = control.getSetting(override_key)
                select = {'1': '0', '2': '1', '3': '2'}.get(override, control.getSetting('hosts.mode'))
            self._autoplay_active = str(select) == '2'
            #Liste der gefundenen Streams
            items = self.getSources(title, year, imdb, season, episode, originaltitle, premiered)
            ## unnötig
            #select = '1' if control.getSetting('downloads') == 'true' and not (control.getSetting('download.movie.path') == '' or control.getSetting('download.tv.path') == '') else select

            # # TODO überprüfen wofür mal gedacht
            # if control.window.getProperty('PseudoTVRunning') == 'True':
            #     return control.resolveUrl(int(sys.argv[1]), True, control.item(path=str(self.sourcesDirect(items))))

            if len(items) > 0:
                if select == '1':
                    from resources.lib.quality_policy import order_sources
                    list_mode = control.getSetting('source.list.sort') or '0'
                    policy = {'0': '0', '1': '3', '2': '2'}.get(str(list_mode), '0')
                    items = order_sources(items, policy)
                    for position, source in enumerate(items):
                        source['label'] = self._build_label(position, source)
                # Optional legacy Kodi directory, otherwise use the redesigned
                # list window even when invoked from external skins/plugins.
                if (select == '1' and control.getSetting('source.list.view', '0') == '1'
                        and 'plugin' in control.infoLabel('Container.PluginName')):
                    control.window.clearProperty(self.itemsProperty)
                    control.window.setProperty(self.itemsProperty, json.dumps(items))
                    
                    control.window.clearProperty(self.metaProperty)
                    control.window.setProperty(self.metaProperty, meta)
                    control.sleep(2)
                    return control.execute('Container.Update(%s?action=addItem&title=%s)' % (sys.argv[0], quote_plus(title)))
                # Source picker, preserving the existing resolution/fallback logic.
                elif select == '1':
                    try:
                        from resources.lib.source_list_window import choose_source
                        picked = choose_source(items, meta)
                    except Exception as exc:
                        log_utils.log('Quellenliste nicht geladen, nutze Standarddialog: %s' % exc,
                                      log_utils.LOGERROR)
                        picked = control.selectDialog([i['label'] for i in items])
                    if picked < 0:
                        return
                    url = self.sourcesDialog(items, start_at=picked)
                    if url == 'close://':
                        return
                elif select == '0':
                    url = self.sourcesDialog(items)
                    if url == 'close://':
                        return
                # Autoplay
                else:
                    url = self.sourcesDirect(items)

            if url == None: return self.errorForSources()

            try: meta = json.loads(meta)
            except: pass

            from resources.lib.player import player
            player().run(title, url, meta)
        except Exception as e:
            log_utils.log('Error %s' % str(e), log_utils.LOGERROR)
        finally:
            self._cancelled.set()
            self.executor.shutdown(wait=False)
            self.resolve_executor.shutdown(wait=False)


# Liste gefundene Streams Indexseite|Hoster
    def addItem(self, title):
        control.playlist.clear()

        items = control.window.getProperty(self.itemsProperty)
        items = json.loads(items)
        if items == None or len(items) == 0: control.idle() ; sys.exit()

        sysaddon = sys.argv[0]
        syshandle = int(sys.argv[1])
        systitle = sysname = quote_plus(title)

        meta = control.window.getProperty(self.metaProperty)
        meta = json.loads(meta)
#TODO
        if meta['mediatype'] == 'movie':
            # downloads = True if control.getSetting('downloads') == 'true' and control.exists(control.translatePath(control.getSetting('download.movie.path'))) else False
            downloads = True if control.getSetting('downloads') == 'true' and control.getSetting('download.movie.path') else False
        else:
            # downloads = True if control.getSetting('downloads') == 'true' and control.exists(control.translatePath(control.getSetting('download.tv.path'))) else False
            downloads = True if control.getSetting('downloads') == 'true' and control.getSetting('download.tv.path') else False

        addonPoster, addonBanner = control.addonPoster(), control.addonBanner()
        addonFanart, settingFanart = control.addonFanart(), control.getSetting('fanart')

        if 'backdrop_url' in meta and 'http' in meta['backdrop_url']: fanart = meta['backdrop_url']
        elif 'fanart' in meta and 'http' in meta['fanart']: fanart = meta['fanart']
        else: fanart = addonFanart

        if 'cover_url' in meta and 'http' in meta['cover_url']: poster = meta['cover_url']
        elif 'poster' in meta and 'http' in meta['poster']: poster = meta['poster']
        else:  poster = addonPoster
        sysimage = poster

        if 'season' in meta and 'episode' in meta:
            sysname += quote_plus(' S%02dE%02d' % (int(meta['season']), int(meta['episode'])))
        elif 'year' in meta:
            sysname += quote_plus(' (%s)' % meta['year'])

        for i in range(len(items)):
            try:
                label = items[i]['label']
                syssource = quote_plus(json.dumps([items[i]]))

                item = control.item(label=label, offscreen=True)
                item.setProperty('IsPlayable', 'true')
                item.setArt({'poster': poster, 'banner': addonBanner})
                if settingFanart == 'true': item.setProperty('Fanart_Image', fanart)

                cm = []
                cm.append(("Medien-Info", 'RunPlugin(%s?action=mediaInfo&source=%s)' % (sysaddon, syssource)))
                cm.append(('Einstellungen Plugin', 'RunPlugin(%s?action=addonSettings)' % sysaddon))
                cm.append(('Einstellungen ResolveURL', 'RunPlugin(%s?action=resolverSettings)' % sysaddon))
                item.addContextMenuItems(cm)

                url = "%s?action=playItem&title=%s&source=%s" % (sysaddon, systitle, syssource)

                ## https://codedocs.xyz/AlwinEsch/kodi/group__python__xbmcgui__listitem.html  # ga0b71166869bda87ad744942888fb5f14
                name = '%s%sStaffel: %s   Episode: %s' % (title, "\n", meta['season'], meta['episode']) if 'season' in meta else title
                plot = meta['plot'] if 'plot' in meta and len(meta['plot'].strip()) >= 1 else ''
                plot = '[COLOR blue]%s[/COLOR]%s%s' % (name, "\n\n", py2_encode(plot))

                if 'duration' in meta:
                    infolable = {'plot': plot,'duration': meta['duration']}
                else:
                    infolable = {'plot': plot}

                # TODO
                # if 'cast' in meta and meta['cast']: item.setCast(meta['cast'])
                # # # remove unsupported InfoLabels
                meta.pop('cast', None)  # ersetzt durch item.setCast(i['cast'])
                meta.pop('number_of_seasons', None)
                meta.pop('imdb_id', None)
                meta.pop('tvdb_id', None)
                meta.pop('tmdb_id', None)

                video_streaminfo = {}
                audio_streaminfo = {}
                if "4k" in items[i]['quality'].lower():
                    video_streaminfo.update({'width': 3840, 'height': 2160})
                elif "1080p" in items[i]['quality'].lower():
                    video_streaminfo.update({'width': 1920, 'height': 1080})
                elif "hd" in items[i]['quality'].lower() or "720p" in items[i]['quality'].lower():
                    video_streaminfo.update({'width': 1280, 'height': 720})
                else:
                    # video_streaminfo.update({"width": 720, "height": 576})
                    video_streaminfo.update({})
                probe = items[i].get('_probe')
                if probe:
                    if probe.get('width') > 5: video_streaminfo.update({'width': probe.get('width'), 'height': probe.get('height')})
                    video_streaminfo.update({'codec': probe.get('video_codec')})
                    if probe.get('has_audio')and probe.get('audio_codec'):
                        audio_streaminfo.update({'codec': probe.get('audio_codec').split(',')[0]})
                    else:
                        audio_streaminfo.update({'codec': ''})

                    if probe.get('duration'):
                        dp = probe['duration'].split(':')
                        try:
                            if len(dp) == 3:
                                dur_secs = int(dp[0]) * 3600 + int(dp[1]) * 60 + int(dp[2])
                            elif len(dp) == 2:
                                dur_secs = int(dp[0]) * 60 + int(dp[1])
                            else:
                                dur_secs = 0
                            if dur_secs > 0:
                                infolable['duration'] = dur_secs
                        except (ValueError, IndexError):
                            pass
                else:
                    ## Quality Video Stream from source.append quality - items[i]['quality']
                    # video_streaminfo ={}
                    # if "4k" in items[i]['quality'].lower():
                    #     video_streaminfo.update({'width': 3840, 'height': 2160})
                    # elif "1080p" in items[i]['quality'].lower():
                    #     video_streaminfo.update({'width': 1920, 'height': 1080})
                    # elif "hd" in items[i]['quality'].lower() or "720p" in items[i]['quality'].lower():
                    #     video_streaminfo.update({'width': 1280,'height': 720})
                    # else:
                    #     # video_streaminfo.update({"width": 720, "height": 576})
                    #     video_streaminfo.update({})

                    ## Codec for Video Stream from extra info - items[i]['info']
                    if 'hevc' in items[i]['label'].lower():
                        video_streaminfo.update({'codec': 'hevc'})
                    elif '265' in items[i]['label'].lower():
                        video_streaminfo.update({'codec': 'h265'})
                    elif 'mkv' in items[i]['label'].lower():
                        video_streaminfo.update({'codec': 'mkv'})
                    elif 'mp4' in items[i]['label'].lower():
                        video_streaminfo.update({'codec': 'mp4'})
                    else:
                        # video_streaminfo.update({'codec': 'h264'})
                        video_streaminfo.update({'codec': ''})

                    ## Quality & Channels Audio Stream from extra info - items[i]['info']
                    # audio_streaminfo = {}
                    if 'dts' in items[i]['label'].lower():
                        audio_streaminfo.update({'codec': 'dts'})
                    elif 'plus' in items[i]['label'].lower() or 'e-ac3' in items[i]['label'].lower():
                        audio_streaminfo.update({'codec': 'eac3'})
                    elif 'dolby' in items[i]['label'].lower() or 'ac3' in items[i]['label'].lower():
                        audio_streaminfo.update({'codec': 'ac3'})
                    else:
                        # audio_streaminfo.update({'codec': 'aac'})
                        audio_streaminfo.update({'codec': ''})

                    ## Channel update ##
                    if '7.1' in items[i].get('info','').lower():
                        audio_streaminfo.update({'channels': 8})
                    elif '5.1' in items[i].get('info','').lower():
                        audio_streaminfo.update({'channels': 6})
                    else:
                        # audio_streaminfo.update({'channels': 2})
                        audio_streaminfo.update({'channels': ''})

                if int(getKodiVersion()) <= 19:
                    item.setInfo(type='Video', infoLabels=infolable)
                    item.addStreamInfo('video', video_streaminfo)
                    item.addStreamInfo('audio', audio_streaminfo)
                else:
                    info_tag = ListItemInfoTag(item, 'video')
                    info_tag.set_info(infolable)
                    stream_details = {
                        'video': [video_streaminfo],
                        'audio': [audio_streaminfo]}
                    info_tag.set_stream_details(stream_details)
                    # info_tag.set_cast(aActors)

                control.addItem(handle=syshandle, url=url, listitem=item, isFolder=False)
            except:
                pass

        control.idle()  # ok
        control.content(syshandle, 'videos')
        control.plugincategory(syshandle, control.addonVersion)
        control.endofdirectory(syshandle, cacheToDisc=True)


    def playItem(self, title, source):
        isDebug = False
        if isDebug: log_utils.log('start playItem', log_utils.LOGWARNING)
        try:
            meta = control.window.getProperty(self.metaProperty)
            meta = json.loads(meta)
            item = json.loads(source)[0]
            if item['source'] == None: raise Exception()
            # control.idle() #ok
            header = control.addonInfo('name')
            progressDialog = control.progressDialog if control.getSetting('progress.dialog') == '0' else control.progressDialogBG
            progressDialog.create(header, '')
            progressDialog.update(0)

            future = self.resolve_executor.submit(self.sourcesResolve, item)

            # edit for captcha
            waiting_time = start_time =30
            if item['provider'] == 'burningseries': waiting_time = start_time = max(5,min(120,int(control.getSetting('captcha.timeout', '120'))))
            hard_deadline=time.monotonic()+start_time
            while waiting_time > 0 and time.monotonic()<hard_deadline:
                try:
                    if control.abortRequested or progressDialog.iscanceled():
                        self._cancelled.set();future.cancel();progressDialog.close();return None
                except:
                    pass
                if future.done(): break
                control.sleep(1)
                waiting_time = waiting_time - 1
                progressDialog.update(int(100 - 100. / start_time * waiting_time), str(item['label']))
                if control.condVisibility('Window.IsActive(virtualkeyboard)') or \
                        control.condVisibility('Window.IsActive(yesnoDialog)'):
                        # or control.condVisibility('Window.IsActive(PopupRecapInfoWindow)'):
                    waiting_time = waiting_time + 1  # dont count down while dialog is presented
                if future.done(): break

            try: progressDialog.close()
            except: pass

            control.execute('Dialog.Close(virtualkeyboard)')
            control.execute('Dialog.Close(yesnoDialog)')

            if not future.done():
                self._cancelled.set();future.cancel()
            resolved_url = future.result() if future.done() and not future.cancelled() else None
            if not resolved_url:
                return
            from resources.lib.player import player
            player().run(title, resolved_url, meta)
            return resolved_url
        except Exception as e:
            log_utils.log('Error %s' % str(e), log_utils.LOGERROR)
        finally:
            self._cancelled.set()
            self.executor.shutdown(wait=False)
            self.resolve_executor.shutdown(wait=False)


    def getSources(self, title, year, imdb, season, episode, originaltitle, premiered, quality='HD', timeout=30):
        search_started=time.monotonic()
        self._collecting=True
        #TODO
        control.idle() #ok
        from resources.lib.source_progress import SourceProgress
        progressDialog = (_NextProgress() if self._series_next_autoplay
                          else SourceProgress(json.loads(self.sysmeta)))
        progressDialog.create(control.addonInfo('name'), '')
        progressDialog.update(0)
        progressDialog.update(0, "Quellen werden vorbereitet")
        try:
            content_typ = 'movies' if season == 0 or season == '' or season == None else 'shows'
            titles = get_titles(title, originaltitle, imdb, content_typ)

            for _provider_name, _provider_obj in self.provider_sources:
                if _provider_name.startswith('MS-'):
                    _provider_obj.tmdb_id = getattr(self, '_mscout_tmdb_id', '')
            sourceDict = [(i[0], i[1], i[1].priority) for i in self.provider_sources]
            random.shuffle(sourceDict)
            sourceDict = fair_provider_order(sourceDict)

            futures = {self.executor.submit(self._getSource, titles, year, season, episode, imdb, provider[0], provider[1]): provider[0] for provider in sourceDict}

            try: timeout = int(control.getSetting('scrapers.timeout'))
            except: pass
            timeout=max(5,min(timeout,120))
            search_deadline=time.monotonic()+timeout
        
            quality = (('0' if control.getSetting('mscout.autoplay.allow4k') != 'false'
                        else control.getSetting('hosts.quality'))
                       if getattr(self, '_autoplay_active', False)
                       else control.getSetting('hosts.quality'))
            if quality == '': quality = '0'

            source_4k = 0
            source_1080 = 0
            source_720 = 0
            source_sd = 0
            total = 0
            total_format = '[COLOR %s]%s[/COLOR]' if control.getSetting('bold_labels') == 'false' else '[COLOR %s][B]%s[/B][/COLOR]'
            pdiag_format = ' 4K: %s | 1080p: %s | 720p: %s | SD: %s | %s: %s                                         '.split('|')


            for i in range(0, 4 * timeout):
                try:
                    if time.monotonic()>=search_deadline:
                        break
                    if control.abortRequested: return sys.exit()
                    try:
                        if progressDialog.iscanceled():
                            self.sources = []
                            return progressDialog.close() or []
                    except:
                        pass

                    if len(self.sources) > 0:
                        if quality in ['0']:
                            source_4k = len([e for e in self.sources if e['quality'] == '4K'])
                            source_1080 = len([e for e in self.sources if e['quality'] in ['1440p','1080p']])
                            source_720 = len([e for e in self.sources if e['quality'] in ['720p','HD']])
                            source_sd = len([e for e in self.sources if e['quality'] not in ['4K','1440p','1080p','720p','HD']])
                        elif quality in ['1']:
                            source_1080 = len([e for e in self.sources if e['quality'] in ['1440p','1080p']])
                            source_720 = len([e for e in self.sources if e['quality'] in ['720p','HD']])
                            source_sd = len([e for e in self.sources if e['quality'] not in ['4K','1440p','1080p','720p','HD']])
                        elif quality in ['2']:
                            source_1080 = len([e for e in self.sources if e['quality'] in ['1080p']])
                            source_720 = len([e for e in self.sources if e['quality'] in ['720p','HD']])
                            source_sd = len([e for e in self.sources if e['quality'] not in ['4K','1440p','1080p','720p','HD']])
                        elif quality in ['3']:
                            source_720 = len([e for e in self.sources if e['quality'] in ['720p','HD']])
                            source_sd = len([e for e in self.sources if e['quality'] not in ['4K','1440p','1080p','720p','HD']])
                        else:
                            source_sd = len([e for e in self.sources if e['quality'] not in ['4K','1440p','1080p','720p','HD']])
                    
                        total = source_4k + source_1080 + source_720 + source_sd

                    source_4k_label = total_format % ('red', source_4k) if source_4k == 0 else total_format % ('lime', source_4k)
                    source_1080_label = total_format % ('red', source_1080) if source_1080 == 0 else total_format % ('lime', source_1080)
                    source_720_label = total_format % ('red', source_720) if source_720 == 0 else total_format % ('lime', source_720)
                    source_sd_label = total_format % ('red', source_sd) if source_sd == 0 else total_format % ('lime', source_sd)
                    source_total_label = total_format % ('red', total) if total == 0 else total_format % ('lime', total)

                    try:
                        info = [name.upper() for future, name in futures.items() if not future.done()]
                        string4 = "Total"
                        if quality in ['0']:
                            line1 = '|'.join(pdiag_format) % (source_4k_label, source_1080_label, source_720_label, source_sd_label, str(string4), source_total_label)
                        elif quality in ['1']:
                            line1 = '|'.join(pdiag_format[1:]) % (source_1080_label, source_720_label, source_sd_label, str(string4), source_total_label)
                        elif quality in ['2']:
                            line1 = '|'.join(pdiag_format[1:]) % (source_1080_label, source_720_label, source_sd_label, str(string4), source_total_label)
                        elif quality in ['3']:
                            line1 = '|'.join(pdiag_format[2:]) % (source_720_label, source_sd_label, str(string4), source_total_label)
                        else:
                            line1 = '|'.join(pdiag_format[3:]) % (source_sd_label, str(string4), source_total_label)

                        if (i / 2) < timeout:
                            string = "Verbleibende Indexseiten: %s"
                        else:
                            string = 'Waiting for: %s'

                        if len(info) > 6: line = line1 + string % (str(len(info)))
                        elif len(info) > 1: line = line1 + string % (', '.join(info))
                        elif len(info) == 1: line = line1 + string % (''.join(info))
                        else: line = line1 + 'Suche beendet!'

                        percent = int(100 * (1-(search_deadline-time.monotonic())/timeout))
                        progressDialog.update(max(1,min(100,percent)), line1 + '[CR]Quellensuche: ' + (', '.join(info[:6]) or 'Abgeschlossen'))

                        if len(info) == 0: break
                        # Quick mode is opt-in: avoids waiting for slow/dead scrapers
                        # once several usable candidates have already appeared.
                        if (control.getSetting('hybrid.fast.search') == 'true'
                                and not (getattr(self, '_autoplay_active', False) and
                                         control.getSetting('mscout.autoplay.quality') in ('', '0', '3'))
                                and i >= 10 and total >= 3):
                            break
                        elif str(control.getSetting('hosts.limit')) == 'true':
                            if total >= int(control.getSetting('hosts.limit.num')): break

                        # if total >= 3 or len(info) == 0: break

                    except Exception as e:
                        log_utils.log('Exception Raised: %s' % str(e), log_utils.LOGERROR)

                    control.sleep(.25)
                except:
                    pass


            try: progressDialog.close()
            except: pass
        finally:
            progressDialog.close()
            self._collecting = False
            scout_futures = [f for f, name in locals().get('futures', {}).items()
                             if name.startswith('MS-')]
            if scout_futures:
                scout_stats = [s for name, s in self.scraper_stats.copy().items()
                               if name.startswith('MS-')]
                log_utils.log('MovieScout Suche: %d geplant, %d gestartet, %d fertig, %d Quellen' % (
                    len(scout_futures), len(scout_stats),
                    sum(s['status'] != 'RUNNING' for s in scout_stats),
                    sum(s['count'] for s in scout_stats)), log_utils.LOGINFO)
            for future in locals().get('futures', {}):
                future.cancel()
            load_timing.record('hybrid','source.search',search_started,count=len(self.sources))
        self.sourcesFilter()
        return self.sources


    def _getSource(self, titles, year, season, episode, imdb, source, call, originaltitle=''):
        if self._cancelled.is_set() or not self._collecting:
            return
        started = time.time()
        self._record_scraper_stat(source, 'RUNNING', 0, 0)
        try:
            if source == 'kinokiste' and season and episode:
                found_sources = call.run(
                    titles, year, season, episode, imdb,
                    originaltitle=originaltitle
                )
            else:
                found_sources = call.run(titles, year, season, episode, imdb)  # kasi self.hostDict
            if self._cancelled.is_set() or not self._collecting:
                return
            if not found_sources:
                self._record_scraper_stat(source, 'EMPTY', 0, time.time() - started)
                return
            found_sources = [json.loads(t) for t in set(json.dumps(d, sort_keys=True) for d in found_sources)]
            for item in found_sources:
                item.update({'provider': source})
                if season and episode:
                    # Serienquellen speichern nur die kleinen Angaben, die zum
                    # frischen Nachladen beim Anklicken benötigt werden. Filme
                    # erhalten diese Felder nicht und bleiben vollständig unverändert.
                    item.update({
                        'isSeries': True,
                        'season': int(season),
                        'episode': int(episode),
                        '_seriesRefresh': {
                            'titles': [str(t) for t in titles[:5]],
                            'originaltitle': str(originaltitle or ''),
                            'year': year,
                            'imdb': imdb,
                            'provider': source
                        }
                    })
                if 'priority' not in item: item.update({'priority': 100})
                if 'prioHoster' not in item: item.update({'prioHoster': 100})
            self.sources.extend(found_sources)
            self._record_scraper_stat(source, 'OK', len(found_sources), time.time() - started)
        except Exception as e:
            error_text = '%s: %s' % (e.__class__.__name__, str(e))
            trace = traceback.format_exc(limit=5).strip()
            if trace and 'NoneType: None' not in trace:
                error_text += ' | ' + trace.replace('\n', ' <- ')
            self._record_scraper_stat(source, 'ERROR', 0, time.time() - started, error_text)


    def _record_scraper_stat(self, provider, status, count, duration, error=''):
        if status != 'RUNNING':
            load_timing.record('hybrid','source.provider',time.monotonic()-float(duration or 0),
                               provider=str(provider),status=status,count=int(count or 0))
        stat = {
            'provider': str(provider),
            'status': str(status),
            'count': int(count or 0),
            'duration_seconds': round(float(duration or 0), 3),
            'error': str(error or '')[:1000],
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        with self.scraper_stats_lock:
            # Ein später TIMEOUT-Eintrag darf ein bereits abgeschlossenes Ergebnis nicht überschreiben.
            old = self.scraper_stats.get(str(provider))
            if old and status == 'TIMEOUT' and old.get('status') != 'RUNNING':
                return
            self.scraper_stats[str(provider)] = stat

    def _write_scraper_report(self, title, year, season, episode, total_duration):
        # Diagnose-Datei und Vollprotokoll wurden aus der Produktionsversion entfernt.
        return

    def sourcesFilter(self):
        # # only german
        _de = ('de', 'deu', 'ger', 'deutsch', 'german', 'german dub', 'ger-sub')
        self.sources = [i for i in self.sources if i['language'].strip().lower() in _de]
        if not self.sources:
            return
        quality = (('0' if control.getSetting('mscout.autoplay.allow4k') != 'false'
                    else control.getSetting('hosts.quality'))
                   if getattr(self, '_autoplay_active', False)
                   else control.getSetting('hosts.quality'))
        if quality == '': quality = '0'
        for i in range(len(self.sources)):
            q = self.sources[i]['quality']
            if q.lower() == 'hd': self.sources[i].update({'quality': '720p'})

        random.shuffle(self.sources)
        #self.sources = sorted(self.sources, key=lambda k: k['prioHoster'], reverse=False)

        filter = []
        if quality in ['0']: filter += [i for i in self.sources if i['quality'] == '4K']
        if quality in ['0', '1']: filter += [i for i in self.sources if i['quality'] == '1440p']
        if quality in ['0', '1', '2']: filter += [i for i in self.sources if i['quality'] == '1080p']
        if quality in ['0', '1', '2', '3']: filter += [i for i in self.sources if i['quality'] == '720p']
        #filter += [i for i in self.sources if i['quality'] in ['SD', 'SCR', 'CAM']]
        filter += [i for i in self.sources if i['quality'] not in ['4K', '4k', '1440p', '1080p', '720p']]
        self.sources = filter

        # Deduplizieren: nur 1 Quelle pro (provider, hoster, quality, info) behalten
        # Scraper liefern oft mehrere CDN-Mirrors fuer den gleichen Hoster
        if control.getSetting('deduped') == 'true':
            seen_keys = set()
            deduped = []
            for s in self.sources:
                key = (s.get('provider', ''), s.get('source', ''), s.get('quality', ''), s.get('info', ''))
                if key not in seen_keys:
                    seen_keys.add(key)
                    deduped.append(s)
            self.sources = deduped
        # Hybrid: keep all valid provider results, even if a remote media probe
        # times out or returns HTTP 403. Kodi resolves the URL fresh on click.
        probe_mode = control.getSetting('hybrid.probe.mode') or '0'
        total_sources = len(self.sources)
        if probe_mode == '1' and total_sources:
            from concurrent.futures import wait, FIRST_COMPLETED
            selected = list(self.sources[:8])
            from resources.lib.source_progress import SourceProgress
            dialog = SourceProgress(json.loads(self.sysmeta))
            dialog.create(control.addonInfo('name'), 'Ermittle Aufloesung und Bitrate ...')
            pool = SearchExecutor(max_workers=2)
            pending = set(pool.submit(self._get_resolution, src) for src in selected)
            deadline = time.monotonic() + 4.0
            try:
                while pending and time.monotonic() < deadline:
                    if control.abortRequested:
                        break
                    try:
                        if dialog.iscanceled():
                            self.sources = []
                            return
                    except Exception:
                        pass
                    finished, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                    for future in finished:
                        try:
                            future.result()
                        except Exception:
                            pass
                    dialog.update(int(100 * (len(selected) - len(pending)) / len(selected)),
                                  'Ermittle Aufloesung und Bitrate ...')
            finally:
                for future in pending:
                    future.cancel()
                pool.shutdown(wait=False)
                try:
                    dialog.close()
                except Exception:
                    pass
        # Probe metadata is optional. No sources are dropped if a video CDN
        # denies the preliminary request (403), lacks bitrate or is too slow.
        self.sources.sort(key=lambda k: (
            (k.get('_probe') or {}).get('height', 0),
            (k.get('_probe') or {}).get('width', 0),
        ), reverse=True)
        try:
            sort_type = int(control.getSetting('hosts.sort') or 0)
        except (TypeError, ValueError):
            sort_type = 0
        if getattr(self, '_autoplay_active', False):
            sort_type = 0  # Autoplay ranks quality later, across BOTH engines.
        if sort_type == 1:
            self.sources.sort(key=lambda k: k.get('provider', ''))
        elif sort_type == 2:
            self.sources.sort(key=lambda k: int(k.get('priority', 100)))
        elif sort_type == 3:
            self.sources.sort(key=lambda k: int(k.get('prioHoster', 100)))

        for idx, source in enumerate(self.sources):
            source['label'] = self._build_label(idx, source)
        return self.sources


    def _get_resolution(self, source):
        # # _getmediainfo
        import time
        from resources.lib import mediainfo
        class _NullDialog:
            def update(self, *a, **kw): pass
            def iscanceled(self): return False
            def close(self): pass

        try:
            if not str(source.get('url') or '').lower().startswith(('http://', 'https://')):
                return
            source_name = source.get('source')
            provider = source.get('provider')
            quality = source.get('quality', '')
            prioHoster = source.get('prioHoster')
            info = source.get('info', '')
            #if provider == 'kinokiste':
            #    import pydevd
            #    pydevd.settrace('localhost', port=12345, stdoutToServer=True, stderrToServer=True)

            resolution = 0
            if quality == '720p': resolution = 1
            elif quality == '1080p': resolution = 2
            info_str = mediainfo.getMediaInfo(source['url'], _NullDialog(), time.time() + 5)
            probe = self._parse_probe_info(info_str)
            if probe:   #  and probe.get('file_size_gb'):
                if not probe.get('height') or not probe['height'] > 0:
                    probe.setdefault('width', resolution)
                    probe.setdefault('height', resolution)

            elif 'Keine Auflösungsinfo' in info_str or 'Stream-Typ konnte nicht erkannt werden' in info_str:
                probe = {}
                probe.setdefault('width', resolution)
                probe.setdefault('height', resolution)
                if prioHoster != 999:
                    info =  source.get('info', '')+ '| ' +'Keine Auflösung'
            else:
                log_utils.log('[BG-Probe] Keine MediaInfo: %s / %s' % (provider, source_name), log_utils.LOGWARNING)
                return
            source.update({'info': info})
        except:
            return
        source.update({'_probe': probe})
        return source


    @staticmethod
    def _build_label(idx, item):
        """Baut Label fuer eine Source — einzige Methode fuer Foreground, BG-Probe und Cache."""
        p = item.get('provider', '?')
        s = item.get('source', '?') # .split('.', 1)[0] # Hoster vollständig anzeigen !!!
        q = item.get('quality', 'SD')
        l = item.get('language', '')
        lang_tag = ''
        _de = ('de', 'deu', 'ger', 'deutsch', 'german', 'german dub', 'ger-sub')
        if l and l.strip().lower() not in _de: lang_tag = l.strip().upper()

        probe = item.get('_probe')

        if probe and probe.get('height') and probe['height'] > 5:   # kasi 0-5 für 720p und so
            # Probe-Daten: echte Aufloesung/Bitrate
            q_display = '%d x %d' % (probe.get('width', 0), probe['height'])
            parts = [_bold(q_display)]
            if probe.get('bitrate'): parts.append(probe['bitrate'])
            # Audio
            has_audio = probe.get('has_audio', True)
            audio = probe.get('audio_lang', '')
            # if not has_audio: # kasi
            #     parts.append('[KEIN AUDIO]')
            if audio and 'DE' not in audio.upper():
                parts.append('[%s]' % audio)
            # Sprache: erst language-Feld, dann Fallback auf info-Feld
            if not lang_tag:
                src_info = item.get('info', '')
                if src_info:
                    lang_part = src_info.rsplit('|', 1)[-1].strip().lower()
                    if lang_part and lang_part not in _de:
                        lang_tag = lang_part.upper()
            if lang_tag:
                parts.append('[%s]' % lang_tag)
            label = '%02d | %s | %s | %s' % (idx+1, _bold(p.upper()), s.upper(), ' | '.join(parts))
        else:
            # Keine Probe: deklarierte Qualitaet + info
            try: f = (' | '.join(['[I]%s [/I]' % info.strip() for info in item.get('info', '').split('|')]))
            except: f = ''
            label = '%02d | %s | ' % (idx+1, _bold(p))
            if q in ('4K', '1440p', '1080p', '720p'): label += '%s | %s' % (s, _bold('[I]%s [/I]' % q))
            elif 'x' in q: label += '%s | %s' % (s, _bold('[I]%s [/I]' % q))
            else:  label += '%s' % s
            if f: label += ' | %s' % f
            if lang_tag: label += ' | %s' % lang_tag
            label = label.replace('| 0 |', '|').replace(' | [I]0 [/I]', '')
            label = re.sub(r'\[I\]\s+\[/I\]', ' ', label)
            label = re.sub(r'\|\s+\|', '|', label)
            label = re.sub(r'\|(?:\s+|)$', '', label)
            label = label.upper()

        if item.get('prioHoster', 0) >= 999:
            label += ' | [COLOR red]CAPTCHA[/COLOR]'
        return label


    @staticmethod
    def _parse_probe_info(info_str):
        """Parst getMediaInfo()-Ergebnis in strukturierte Daten."""
        result = {}
        if not info_str:
            return result
        for line in info_str.split('\n'):
            if 'sung:' in line:  # Auflösung / Aufloesung
                m = re.search(r'(\d+)x(\d+)', line)
                if m:
                    result['width'] = int(m.group(1))
                    result['height'] = int(m.group(2))
            elif 'Video-Codec:' in line:
                result['video_codec'] = line.split(':', 1)[1].strip()
            elif 'Bitrate:' in line:
                result['bitrate'] = line.split(':', 1)[1].strip()
            elif 'Dauer:' in line:
                # "Dauer:  1:32:45" oder "Dauer:  45:30 (geschätzt)"
                m = re.search(r'(\d+:\d{2}(?::\d{2})?)', line)
                if m:
                    result['duration'] = m.group(1)
            elif 'Dateigr' in line:  # Dateigröße / Dateigroesse
                m = re.search(r'([\d.]+)\s*(GB|MB)', line)
                if m:
                    val = float(m.group(1))
                    if m.group(2) == 'MB':
                        val /= 1024.0
                    result['file_size_gb'] = val
            elif 'Audio:' in line:
                audio = line.split(':', 1)[1].strip()
                if '!! Kein Audio' in audio:
                    result['has_audio'] = False
                else:
                    result['has_audio'] = True
                    # Sprache extrahieren (z.B. "AAC, Stereo -- Deutsch" -> "DE")
                    # Multi-Track: alle Sprachen sammeln (z.B. "DE/EN")
                    # Manche Streams nutzen englische Sprachnamen (German statt Deutsch)
                    _LANG_NORMALIZE = {
                        'GE': 'DE', 'SP': 'ES', 'JA': 'JA', 'CH': 'ZH',
                        'DU': 'NL', 'CZ': 'CS', 'GR': 'EL',
                    }
                    if ' -- ' in audio:
                        lang_part = audio.split(' -- ', 1)[1].strip()
                        lang_code = lang_part[:2].upper()
                        lang_code = _LANG_NORMALIZE.get(lang_code, lang_code)
                        if 'audio_lang' in result:
                            if lang_code not in result['audio_lang']:
                                result['audio_lang'] += '/%s' % lang_code
                        else:
                            result['audio_lang'] = lang_code
                    else:
                        result['audio_codec'] = audio
        return result




    def sourcesResolve(self, item, info=False):
        if self._cancelled.is_set():return None
        try:
            self.url = None
            url = item['url']
            direct = item['direct']
            local = item.get('local', False)
            provider = item['provider']
            call = [i[1] for i in self.provider_sources if i[0] == provider][0]
            url = call.resolve(url)
            if self._cancelled.is_set():return None

            if not direct == True:
                try:
                    hmf = resolver.HostedMediaFile(url=url, include_disabled=True, include_universal=False, include_popups=False)
                    if not hmf.valid_url():
                        hmf = resolver.HostedMediaFile(url=url, include_disabled=True, include_universal=False, include_popups=True)
                    if hmf.valid_url():
                        if self._cancelled.is_set():return None
                        url = hmf.resolve()
                        if url == False or url == None or url == '': url = None  # raise Exception()
                except:
                    url = None
            elif item.get('prioHoster', 0) >= 999:
                try:
                    hmf = resolver.HostedMediaFile(url=url, include_disabled=True, include_universal=False, include_popups=True)
                    if hmf.valid_url():
                        if self._cancelled.is_set():return None
                        url = hmf.resolve()
                        if url == False or url == None or url == '': url = None
                except:
                    url = None

            if url == None or (not '://' in str(url) and not local):
                log_utils.log('Kein Video Link gefunden: Provider %s / %s / %s ' % (item['provider'], item['source'], str(item['source'])), log_utils.LOGERROR)
                raise Exception()

            # if not utils.test_stream(url):
            #     log_utils.log('URL Test Error: %s' % url, log_utils.LOGERROR)
            #     raise Exception()

            # url = utils.m3u8_check(url)

            if url:
                # No sensitive signed URL, token or cookie in the logs.
                try:
                    from urllib.parse import urlparse
                    media_url = str(url).split('|', 1)[0]
                    host = urlparse(media_url).hostname or 'unbekannt'
                    fmt = 'HLS' if '.m3u8' in media_url.lower() else ('DASH' if '.mpd' in media_url.lower() else 'Datei/Hoster')
                    log_utils.log('[HybridPlayback] Provider=%s Hoster=%s Ziel=%s Typ=%s Header=%s' % (
                        provider, item.get('source', ''), host, fmt, 'ja' if '|' in str(url) else 'nein'), log_utils.LOGINFO)
                except Exception:
                    pass
                if self._cancelled.is_set():return None
                self.url = url
                return url
            else:
                raise Exception()
        except:
            if info: self.errorForSources()
            return


    def sourcesDialog(self, items, start_at=None):
        # start_at is provided by the poster list; otherwise keep old Dialog.
        if start_at is None:
            labels = [i['label'] for i in items]
            select = control.selectDialog(labels)
        else:
            select = int(start_at)
        if select < 0 or select >= len(items):
            return 'close://'

        # A manual click selects exactly one source, not forty hidden fallbacks.
        items = [items[select]]

        header = control.addonInfo('name')
        header2 = header.upper()

        progressDialog = control.progressDialog if control.getSetting('progress.dialog') == '0' else control.progressDialogBG
        progressDialog.create(header, '')
        progressDialog.update(0)

        block = None

        try:
            for i in range(len(items)):
                try:
                    if items[i]['source'] == block: raise Exception()

                    future = self.resolve_executor.submit(self.sourcesResolve, items[i])

                    try:
                        if progressDialog.iscanceled(): break
                        progressDialog.update(int((100 / float(len(items))) * i), str(items[i]['label']))
                    except:
                        progressDialog.update(int((100 / float(len(items))) * i), str(header2) + str(items[i]['label']))

                    waiting_time = 30
                    hard_deadline=time.monotonic()+30
                    while waiting_time > 0 and time.monotonic()<hard_deadline:
                        try:
                            if control.abortRequested: return sys.exit() #xbmc.Monitor().abortRequested()
                            if progressDialog.iscanceled(): return progressDialog.close()
                        except:
                            pass

                        if future.done(): break
                        control.sleep(1)

                        waiting_time = waiting_time - 1

                        if control.condVisibility('Window.IsActive(virtualkeyboard)') or \
                                control.condVisibility('Window.IsActive(yesnoDialog)') or \
                                control.condVisibility('Window.IsActive(ProgressDialog)'):
                            waiting_time = waiting_time + 1 #dont count down while dialog is presented ## control.condVisibility('Window.IsActive(PopupRecapInfoWindow)') or \

                    if not future.done():
                        block = items[i]['source']
                        self._cancelled.set()
                        future.cancel()
                        raise Exception()
                    resolved_url = future.result()
                    if not resolved_url:
                        raise Exception()

                    self.selectedSource = items[i]['label']

                    try: progressDialog.close()
                    except: pass

                    control.execute('Dialog.Close(virtualkeyboard)')
                    control.execute('Dialog.Close(yesnoDialog)')
                    return resolved_url
                except:
                    pass

            try: progressDialog.close()
            except: pass

        except Exception as e:
            try: progressDialog.close()
            except: pass
            log_utils.log('Error %s' % str(e), log_utils.LOGINFO)


    def sourcesDirect(self, items):
        from resources.lib.quality_policy import order_sources
        items = order_sources(items, control.getSetting('mscout.autoplay.quality') or '0')[:3]
        # TODO - OK
        # filter = [i for i in items if i['source'].lower() in self.hostcapDict and i['debrid'] == '']
        # items = [i for i in items if not i in filter]
        # items = [i for i in items if ('autoplay' in i and i['autoplay'] == True) or not 'autoplay' in i]

        u = None

        header = control.addonInfo('name')
        header2 = header.upper()

        try:
            progressDialog = control.progressDialogBG
            progressDialog.create(header, '')
            progressDialog.update(0)
        except:
            pass

        for i in range(len(items)):
            try:
                if progressDialog.iscanceled(): break
                progressDialog.update(int((100 / float(len(items))) * i), str(items[i]['label']))
            except:
                progressDialog.update(int((100 / float(len(items))) * i), str(header2) + str(items[i]['label']))

            try:
                if control.abortRequested: return sys.exit()

                future=self.resolve_executor.submit(self.sourcesResolve,items[i])
                deadline=time.monotonic()+30
                monitor=xbmc.Monitor()
                while not future.done() and time.monotonic()<deadline:
                    if self._cancelled.is_set() or control.abortRequested or progressDialog.iscanceled():
                        self._cancelled.set();future.cancel();break
                    if monitor.waitForAbort(0.25):
                        self._cancelled.set();future.cancel();break
                if self._cancelled.is_set():break
                if not future.done():
                    self._cancelled.set();future.cancel();break
                url=future.result()
                if u == None: u = url
                if not url == None: break
            except:
                pass

        try: progressDialog.close()
        except: pass

        return u

    def errorForSources(self):
        control.idle()
        control.infoDialog("Keine Streams verfügbar oder ausgewählt", sound=False, icon='INFO')
  
    # def getTitle(self, title):
    #     title = utils.normalize(title)
    #     return title

    def getConstants(self):
        self.itemsProperty = '%s.container.items' % control.Addon.getAddonInfo('id')
        self.metaProperty = '%s.container.meta'  % control.Addon.getAddonInfo('id')
        from scrapers import sources
        self.provider_sources = sources()
        if control.getSetting('mscout.enable') == 'true':
            try:
                from resources.lib.movie_scout.bridge import get_providers
                self.provider_sources.extend(get_providers())
            except Exception as exc:
                log_utils.log('MovieScout-Engine Ladefehler: %s' % type(exc).__name__, log_utils.LOGWARNING)

# https://github.com/michaz1988/michaz1988.github.io/issues/48
    def mediaInfo(self, source, dialog=None):
        import xbmcgui
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except: pass
        try:
            item = json.loads(source)[0]
            if item['source'] is None:
                raise Exception()

            import time as _time
            from resources.lib.mediainfo import TOTAL_TIMEOUT
            deadline = _time.time() + TOTAL_TIMEOUT

            if dialog is None:
                dialog = xbmcgui.DialogProgress()
                dialog.create('Medien-Info', 'Löse Stream-URL auf... (%d Sek.)' % TOTAL_TIMEOUT)
                dialog.update(0)

            future = self.resolve_executor.submit(self.sourcesResolve, item)

            # Wait for resolve with responsive cancel (check every 250ms)
            for i in range(120):  # 120 * 250ms = 30s max
                remaining = int(deadline - _time.time())
                if remaining > 0:
                    dialog.update(int(50.0 * i / 120), 'Löse Stream-URL auf... (%d Sek.)' % remaining)
                else:
                    dialog.update(int(50.0 * i / 120), 'Löse Stream-URL auf...')
                try:
                    if dialog.iscanceled():
                        try: dialog.close()
                        except: pass
                        return
                except: pass
                if future.done():
                    break
                control.sleep(0.25)
                # Don't count down while resolver shows interactive dialogs
                if control.condVisibility('Window.IsActive(virtualkeyboard)') or \
                        control.condVisibility('Window.IsActive(yesnoDialog)'):
                    continue

            url = self.url if future.done() else None
            control.execute('Dialog.Close(virtualkeyboard)')
            control.execute('Dialog.Close(yesnoDialog)')

            try:
                if dialog.iscanceled():
                    try: dialog.close()
                    except: pass
                    return
            except: pass

            if url is None:
                try: dialog.close()
                except: pass
                control.infoDialog("Stream-URL konnte nicht aufgelöst werden", sound=False, icon='INFO')
                return

            dialog.update(50, 'Analysiere Stream...')

            from resources.lib import mediainfo
            info = mediainfo.getMediaInfo(url, dialog, deadline)

            try: dialog.close()
            except: pass

            if info:
                xbmcgui.Dialog().textviewer('Medien-Info', info)
            else:
                control.infoDialog("Auflösung konnte nicht ermittelt werden", sound=False, icon='INFO')

        except Exception as e:
            try:
                if dialog: dialog.close()
            except: pass
            log_utils.log('mediaInfo Error: %s' % str(e), log_utils.LOGERROR)
            control.infoDialog("Auflösung konnte nicht ermittelt werden", sound=False, icon='INFO')
