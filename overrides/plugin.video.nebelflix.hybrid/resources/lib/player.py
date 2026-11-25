

#2021-11-22
#edit 2026-06-15

import sys, re, time, json, threading
import hashlib,os,codecs
from sqlite3 import dbapi2 as database
import xbmc, xbmcplugin, xbmcgui
from resources.lib.control import translatePath
from resources.lib import log_utils, control, playcountDB, hybrid_history
import xmlrpc.client as _xmlrpclib
from io import BytesIO as _io
from urllib.parse import urlencode

# eventuell zur späteren verwendung als meta
#_params = dict(parse_qsl(sys.argv[2].replace('?',''))) if len(sys.argv) > 1 else dict()

class player(xbmc.Player):
    def __init__(self, *args, **kwargs):
        xbmc.Player.__init__(self, *args, **kwargs)
        self.streamFinished = False
        self._startup_failed = False
        self._ever_started = False
        self._user_stopped = False
        self.totalTime = 0
        self.currentTime = 0
        self.playcount = 0
        self.watcher_control = False
        self._history_last_write = 0
        self._history_written = False
        self._next_episode_after_playback = False
        self._next_data = None
        self._next_ready = False
        self._next_prompt_shown = False
        self._next_prompt = None
        self._next_action = ''
        self._handoff_started = False
        self.isdebug = True if control.getSetting('status.debug') == 'true' else False


    def run(self, title, url, meta, direct=False, cancel_start=None):
        import xbmc
        try:
            self.streamFinished = False
            self._startup_failed = False
            self._ever_started = False
            self._user_stopped = False
            self._startup_cancel=cancel_start
            self.meta = meta
            self._next_episode_after_playback = False
            self._next_data = None
            self._next_ready = False
            self._next_prompt_shown = False
            self._next_prompt = None
            self._next_action = ''
            self._handoff_started = False
            self.mediatype = meta['mediatype']
            self.title = meta['title']
            self.year = str(meta['year']) if 'year' in meta else ''
            if meta['mediatype'] == 'movie':
                self.name = title + ' (%s)' % meta['year'] if meta.get('year', False) else title
            else:
                self.name = title + ' S%02dE%02d' % (int(meta['season']), int(meta['episode']))

            self.imdb = meta['imdb_id'] if 'imdb_id' in meta else None
            self.number_of_seasons = meta['number_of_seasons'] if 'number_of_seasons' in meta else None
            self.season = meta['season'] if 'season' in meta else None
            self.number_of_episodes = meta['number_of_episodes'] if 'number_of_episodes' in meta else None
            self.episode = meta['episode'] if 'episode' in meta else None

            self.playcount = meta['playcount'] if 'playcount' in meta else 0
            self._history_meta = dict(meta, history_name=self.name)
            self._history_last_write = 0
            self._history_written = False
            try:
                previous = hybrid_history.find(self._history_meta)
            except Exception as exc:
                xbmc.log('[Nebelflix Hybrid] History unavailable: %s' % exc, xbmc.LOGWARNING)
                previous = None
            # The unified history controls Resume. Legacy bookmarks are used
            # only for titles not yet present in the new history database.
            self.offset = (str(previous['position']) if previous and not previous['completed']
                           else '0' if previous else bookmarks().get(self.name))

            from glob import glob
            os.chdir(os.path.join(control.translatePath('special://database/')))
            self.videoDB = os.path.join(control.translatePath('special://database/'), sorted(glob("MyVideos*.db"), reverse=True)[0])

            self.fileID = self.getVideoDB()

            plot = control.unquote(meta['plot']) if 'plot' in meta else ''

            Info = {'plot': plot}
            if 'imdbnumber' in meta: Info.setdefault('IMDBNumber', meta['imdbnumber'])
            if meta['mediatype'] == 'movie':
                Info.setdefault('OriginalTitle', meta['title'])
                Info.setdefault('year', meta['year'])
            else:
                Info.setdefault('TVshowtitle', meta['title'])
                Info.setdefault('Season', self.season)
                Info.setdefault('Episode', self.episode)

            if meta['mediatype'] == 'movie':
                item = control.item(label=self.title)
            else:
                item = control.item(label=self.name)

            # TS: video/mp2t
            # HLS: application/x-mpegURL or application/vnd.apple.mpegurl
            # Dash: application/dash+xml
            kodiver = int(xbmc.getInfoLabel("System.BuildVersion").split(".")[0])
            # Header (url|User-Agent=...&Referer=...) zuerst abtrennen. maxsplit=1,
            # damit URLs mit mehreren | nicht crashen (Fix ggue 0.9er .split('|')).
            strhdr = None
            if '|' in url:
                url, strhdr = url.split('|', 1)
            # HLS UND DASH komplett ueber InputStream Adaptive (wie 0.9er).
            # ISA-Auto-Install passiert beim Kodi-Start im service.py, nicht hier.
            if ".m3u" in url or '.mpd' in url:
                item.setProperty("inputstream", "inputstream.adaptive")
                item.setProperty('inputstream.adaptive.config', '{"ssl_verify_peer":false}')
                if '.mpd' in url:
                    if kodiver < 21: item.setProperty('inputstream.adaptive.manifest_type', 'mpd')
                    item.setMimeType('application/dash+xml')
                else:
                    if kodiver < 21: item.setProperty('inputstream.adaptive.manifest_type', 'hls')
                    item.setMimeType('application/x-mpegURL')
                item.setContentLookup(False)
                if strhdr:
                    item.setProperty('inputstream.adaptive.stream_headers', strhdr)
                    if kodiver > 19: item.setProperty('inputstream.adaptive.manifest_headers', strhdr)
            else:
                # Sonstige (mp4 etc.): Header ggf. wieder anhaengen, sonst clean durchreichen.
                if strhdr:
                    url = url + '|' + strhdr
            item.setPath(url)
            item.setProperty('IsPlayable', 'true')
            info = {'title': self.title, 'plot': plot,
                    'mediatype': 'episode' if self.episode is not None else 'movie',
                    'year': int(meta.get('year') or 0)}
            if self.episode is not None:
                info.update(tvshowtitle=meta.get('tvshowtitle', self.title),
                            season=int(self.season or 0), episode=int(self.episode))
            item.setArt({key: str(meta.get(key) or '') for key in
                         ('poster', 'fanart', 'thumb', 'clearlogo')})
            if kodiver >= 20:
                from resources.lib.listitem import ListItemInfoTag
                tag = ListItemInfoTag(item, 'video')
                tag.set_info(info)
                ids = {key: str(value) for key, value in
                       (('tmdb', meta.get('tmdb_id')), ('imdb', self.imdb)) if value}
                tag.set_unique_ids(ids, 'tmdb' if ids.get('tmdb', '').isdigit() else 'imdb')
            else:
                item.setInfo('video', info)

            import xbmcgui
            xbmcgui.Window(10000).setProperty('NF.PlaybackOwner','manual')
            xbmcgui.Window(10000).setProperty('xShip.PlayerInfoString', json.dumps({
                'source_hash': hashlib.sha256(str(url).split('|',1)[0].encode('utf8')).hexdigest(),
                'tmdb_type': 'tv' if self.episode is not None else 'movie',
                'tmdb_id': str(meta.get('tmdb_id') or ''),
                'season': self.season, 'episode': self.episode}))
            if int(sys.argv[1]) > 0 and not direct:
                xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, item)
            else:
                xbmc.Player().play(url, item)
            self.keepPlaybackAlive()
            # Give Kodi's natural-ended callback a brief chance to run if the
            # watcher noticed player shutdown a fraction of a second earlier.
            # Manual Stop never sets _next_episode_after_playback.
            if self.episode is not None and not self._handoff_started:
                xbmc.sleep(350)
            if self._next_episode_after_playback:
                self._offer_next_episode()
            return
        except Exception as exc:
            self.onPlayBackError()
            xbmc.log('[Nebelkind Hybrid] Player beendet: '+type(exc).__name__,xbmc.LOGWARNING)
            return


    def _wait_for_start(self,monitor,timeout=30):
        deadline=time.monotonic()+timeout
        while not self.streamFinished and not monitor.abortRequested():
            cancel=getattr(self,'_startup_cancel',None)
            if callable(cancel) and cancel():
                self._user_stopped=True;self.streamFinished=True
                self.stop()
                return False
            if self.isPlayingVideo():
                self._ever_started=True
                return True
            if time.monotonic()>=deadline:
                self.onPlayBackError()
                # Stop only a still-unstarted attempt. Never stop an active video.
                if not self.isPlayingVideo():self.stop()
                return False
            if monitor.waitForAbort(0.25):return False
        return False

    def onPlayBackError(self):
        self.streamFinished=True
        self._startup_failed=True
        self._next_episode_after_playback=False
        self._next_data=None
        self._next_action=''
        self._close_next_prompt()
        xbmc.log('[Nebelkind Hybrid] Wiedergabe fehlgeschlagen; bereits gestartet=%s, Benutzer-Stopp=%s' % (getattr(self,'_ever_started',False),getattr(self,'_user_stopped',False)),xbmc.LOGWARNING)

    def keepPlaybackAlive(self):
        if self.isdebug: log_utils.log('Start - keepPlaybackAlive', log_utils.LOGINFO)
        monitor=xbmc.Monitor()
        if not self._wait_for_start(monitor):return
        idle_since=None

        if self.isPlayingVideo():
            try:
                playcountDB.createEntry(self.mediatype, self.title, self.name, self.imdb, self.number_of_seasons, self.season, self.number_of_episodes, self.episode, tmdb_id=self._history_meta.get('tmdb_id'))
            except:
                pass

        monitor = xbmc.Monitor()
        self.watcher_control = False
        if self.episode is not None and control.getSetting('series.upnext.enabled') != 'false':
            threading.Thread(target=self._prefetch_next, daemon=True).start()
        while not monitor.abortRequested() and not self.streamFinished:
            if self.isPlayingVideo():
                idle_since=None
                self.totalTime = self.getTotalTime()
                self.currentTime = self.getTime()
                if self.totalTime > 0:
                    if (self.episode is not None
                            and control.getSetting('series.upnext.enabled') != 'false'
                            and self.currentTime / self.totalTime >= 0.90
                            and not self._next_prompt_shown):
                        self._show_next_prompt()
                    if self._next_action == 'play' and not self._handoff_started:
                        if self._launch_next(immediate=True):
                            self.streamFinished = True
                            break
                    watcher = hybrid_history.is_complete(
                        self.currentTime, self.totalTime,
                        'episode' if self.episode is not None else 'movie')
                    if watcher and not self.watcher_control:
                        playcountDB.updatePlaycount(self.mediatype, self.title, self.name, self.imdb,
                            self.number_of_seasons, self.season, self.number_of_episodes, self.episode, 1)
                        self.watcher_control = True
                    # Persist while playback is active, not just at shutdown.
                    if time.monotonic() - self._history_last_write >= 10:
                        try:
                            hybrid_history.save(self._history_meta, self.currentTime,
                                                self.totalTime, completed=self.watcher_control)
                            self._history_last_write = time.monotonic()
                        except Exception as exc:
                            xbmc.log('[Nebelflix Hybrid] History update: %s' % exc, xbmc.LOGWARNING)
            else:
                if idle_since is None:idle_since=time.monotonic()
                elif time.monotonic()-idle_since>=3:
                    self.streamFinished=True
                    self._next_episode_after_playback=False
                    self._close_next_prompt()
                    break
            if monitor.waitForAbort(0.5):break

        if self.isdebug: log_utils.log('Ende - keepPlaybackAlive', log_utils.LOGINFO)


    def idleForPlayback(self):
        for i in range(0, 200):
            if control.condVisibility('Window.IsActive(busydialog)') == 1: control.idle()
            else: break
            xbmc.sleep(100)

    def onPlayBackStarted(self):
        if self.isdebug: log_utils.log('Start - onPlayBackStarted', log_utils.LOGINFO)
        # Keep the source overlay until Kodi confirms AV readiness.

    def onAVStarted(self):
        self._ever_started=True
        if self.isdebug: log_utils.log('Start - onAVStarted', log_utils.LOGINFO)
        control.execute('Dialog.Close(all,true)')
        if not self.offset == '0': self.seekTime(float(self.offset))
        self.idleForPlayback()
        if control.getSetting('subtitles') == 'true':
            status = subtitles().get(self.name, self.imdb, self.season, self.episode)
            ## Subtitles in Player Menü ausschalten - wird dann bei Bedarf per "Hand" eingeschaltet
            # xbmc.executeJSONRPC('{"jsonrpc": "2.0", "method": "Player.SetSubtitle", "params": {"playerid": 1, "subtitle" : "on"}, "id": "1"}')
            xbmc.executeJSONRPC('{"jsonrpc": "2.0", "method": "Player.SetSubtitle", "params": {"playerid": 1, "subtitle" : "off"}, "id": "1"}')
        if self.isdebug: log_utils.log('Ende - onAVStarted', log_utils.LOGINFO)


    def onPlayBackStopped(self):
        if not self._startup_failed:self._user_stopped=True
        self.streamFinished=True
        if self._startup_failed:return
        self._close_next_prompt()
        if self.isdebug: log_utils.log('Start - onPlayBackStopped', log_utils.LOGINFO)
        if not self._history_written:
            try:
                # Refresh last position to avoid losing the last 10 seconds.
                if self.isPlayingVideo():
                    self.currentTime = self.getTime()
                    self.totalTime = self.getTotalTime()
                finished = hybrid_history.is_complete(
                    self.currentTime, self.totalTime,
                    'episode' if self.episode is not None else 'movie')
                if self.watcher_control or finished:
                    if not self.watcher_control:
                        playcountDB.updatePlaycount(self.mediatype, self.title, self.name, self.imdb,
                            self.number_of_seasons, self.season, self.number_of_episodes, self.episode, 1)
                    self.watcher_control = True
                hybrid_history.save(self._history_meta, self.currentTime, self.totalTime,
                                    completed=self.watcher_control)
                self._history_written = True
                xbmcgui.Window(10000).setProperty('NF.RefreshWidgets', str(time.time_ns()))
            except Exception as exc:
                xbmc.log('[Nebelflix Hybrid] History finalize: %s' % exc, xbmc.LOGWARNING)
        self.runVideoDB()
        self.streamFinished = True
        if self.isdebug: log_utils.log('vor parentDir - onPlayBackStopped', log_utils.LOGINFO)
        if self.watcher_control:
            bookmarks().remove(self.name)
            if not self._handoff_started and not self._next_episode_after_playback:
                self.parentDir()
            self.watcher_control = False
        else:  # bookmark setzen
            bookmarks().save(self.currentTime, self.name)
        if self.isdebug: log_utils.log('Ende - onPlayBackStopped', log_utils.LOGINFO)

    def onPlayBackEnded(self):
        # Set this BEFORE cleanup; otherwise parentDir() navigates away while
        # the new episode is about to start, breaking remote/TV UI state.
        if (self.mediatype != 'movie' and not self._handoff_started
                and control.getSetting('series.next.autoplay') != 'false'
                and (self._next_data is not None or self._has_next_episode())):
            self._next_episode_after_playback = True
        self.onPlayBackStopped()
        if self.isdebug: log_utils.log('Ende - onPlayBackEnded', log_utils.LOGINFO)

    def _has_next_episode(self):
        try:
            season = int(self.season)
            episode = int(self.episode)
            episodes = int(self.number_of_episodes) if self.number_of_episodes not in (None, '', 'None') else 0
            seasons = int(self.number_of_seasons) if self.number_of_seasons not in (None, '', 'None') else 0
            if episodes and episode < episodes:
                return True
            if episodes and episode >= episodes and seasons and season < seasons:
                return True
        except Exception:
            return False
        return False

    def _next_episode_meta(self):
        try:
            meta = dict(self.meta)
            season = int(self.season)
            episode = int(self.episode)
            episodes = int(self.number_of_episodes) if self.number_of_episodes not in (None, '', 'None') else 0
            if episodes and episode < episodes:
                meta['episode'] = episode + 1
            else:
                meta['season'] = season + 1
                meta['episode'] = 1
                # Die Episodenanzahl gehört zur bisherigen Staffel und darf für
                # die neue Staffel nicht als Grenze weiterverwendet werden.
                meta.pop('number_of_episodes', None)
            meta['playcount'] = 0
            meta.pop('overlay', None)
            # Keep manual source selection unless the user chose Autoplay.
            meta['select'] = '2' if self.meta.get('_autoplay_queue') else '1'
            # Clear stale flags when this episode originated in an older build.
            meta.pop('_series_next_autoplay', None)
            return meta
        except Exception:
            return None

    def _prefetch_next(self):
        """Network requests must not stall playback or the 90% prompt."""
        try:
            from resources.lib.next_episode import next_episode
            if self.streamFinished:return
            result=next_episode(dict(self.meta))
            if self.streamFinished:return
            self._next_data=result
            if self._next_data:
                info = self._next_data['next_episode']
                xbmc.log('[Nebelflix Hybrid] Nachfolger bereit: S%02dE%02d' %
                         (info['season'], info['episode']), xbmc.LOGINFO)
        except Exception as exc:
            xbmc.log('[Nebelflix Hybrid] Nachfolger TMDb: %s' % exc, xbmc.LOGWARNING)
        finally:
            self._next_ready = True

    def _on_next_now(self):
        self._next_action = 'play'

    def _on_next_later(self):
        self._next_action = 'later'

    def _close_next_prompt(self):
        popup = self._next_prompt
        self._next_prompt = None
        if popup is not None:
            try:
                popup.close()
            except Exception:
                pass

    def _next_target(self):
        if self._next_data:
            return self._next_data.get('play_url') or ''
        # Bounded fallback if TMDb metadata has not arrived; do not infer a
        # future season when no episode count exists.
        if not self._has_next_episode():
            return ''
        meta = self._next_episode_meta()
        if not meta:
            return ''
        return 'plugin://plugin.video.nebelflix.hybrid/?' + urlencode({
            'action': 'play',
            'sysmeta': json.dumps(meta, ensure_ascii=False, separators=(',', ':'))})

    def _show_next_prompt(self):
        # Only show once per playing episode. No 90%-time fetch/GUI blocking.
        if self._next_prompt_shown:
            return
        if self._next_data is None and not self._has_next_episode():
            return
        self._next_prompt_shown = True
        try:
            info = (self._next_data or {}).get('next_episode') or {}
            meta = self._next_episode_meta() or {}
            season = int(info.get('season') or meta.get('season') or self.season or 1)
            episode = int(info.get('episode') or meta.get('episode') or 1)
            title = '%s · S%02dE%02d' % (self.title, season, episode)
            subtitle = info.get('title') or 'Nach dieser Folge automatisch weiterschauen'
            from resources.lib.next_prompt import NextPrompt
            popup = NextPrompt(title, subtitle,
                               self._on_next_now, self._on_next_later)
            self._next_prompt = popup
            popup.show()
            xbmc.log('[Nebelflix Hybrid] Eigenes Up Next bei 90%% angezeigt', xbmc.LOGINFO)
        except Exception as exc:
            xbmc.log('[Nebelflix Hybrid] Next UI: %s' % exc, xbmc.LOGWARNING)
            self._next_prompt = None

    def _launch_next(self, immediate=False):
        if self.mediatype == 'movie' or self._handoff_started:
            return False
        url = self._next_target()
        if not url:
            xbmc.log('[Nebelflix Hybrid] Kein bestätigter Nachfolger zum Starten', xbmc.LOGWARNING)
            return False
        self._handoff_started = True
        self._next_episode_after_playback = False
        self._close_next_prompt()
        try:
            try:
                info = (self._next_data or {}).get('next_episode') or {}
                meta = self._next_episode_meta() or {}
                season = int(info.get('season') or meta.get('season') or 0)
                episode = int(info.get('episode') or meta.get('episode') or 0)
                xbmcgui.Dialog().notification('Nebelflix · Nächste Folge',
                    'S%02dE%02d · Suche Wiedergabequelle …' % (season, episode),
                    xbmcgui.NOTIFICATION_INFO, 4500, False)
            except Exception:
                pass
            if immediate and self.isPlayingVideo():
                self.stop()
            xbmc.log('[Nebelflix Hybrid] Folgewechsel: %s' %
                     ('JETZT' if immediate else 'ENDE'), xbmc.LOGINFO)
            xbmc.executebuiltin('RunPlugin(%s)' % url)
            return True
        except Exception as exc:
            self._handoff_started = False
            xbmc.log('[Nebelflix Hybrid] Folgewechsel fehlgeschlagen: %s' % exc, xbmc.LOGERROR)
            return False

    def _offer_next_episode(self):
        # Natural EOS only; manual Stop never triggers this path.
        if control.getSetting('series.next.autoplay') != 'false':
            self._launch_next(immediate=False)



    def parentDir(self):
        """Navigiert nach Filmende zurück zur Filmliste und aktualisiert Playcount-Anzeige.

        Im Verzeichnis-Modus (hosts.mode='1') erzeugt die Quellenauswahl eine extra
        Navigationsebene. Nach dem Abspielen steht der User in der Quellenliste
        (Container.Content='videos') und muss per ParentDir zurück zur Filmliste
        (Container.Content='movies') navigiert werden.
        """
        try:
            monitor = xbmc.Monitor()
            hosts_mode = control.getSetting('hosts.mode')
            # Verzeichnis-Modus: Quellenliste -> zurück zur Filmliste
            if hosts_mode == '1':
                if self.isdebug: log_utils.log(__name__ + ' - parentDir: start (hosts_mode=%s)' % hosts_mode, log_utils.LOGINFO)
                # Warte bis Kodi die Quellenliste wieder anzeigt (nach Player-Close)
                if not self._wait_for_content('videos', monitor, timeout=5.0):
                    if self.isdebug: log_utils.log(__name__ + ' - parentDir: Timeout waiting for videos', log_utils.LOGINFO)
                    return
                # Eine Ebene hoch: Quellenliste -> Filmliste
                if control.getInfoLabel("Container.Content") != 'movies':
                    control.execute('Action(ParentDir)')
                    if not self._wait_for_content('movies', monitor, timeout=3.0):
                        if self.isdebug: log_utils.log(__name__ + ' - parentDir: Timeout waiting for movies', log_utils.LOGINFO)
                        return

            # Playcount-Häkchen sichtbar machen (nur bei erstmaliger Wiedergabe)
            if self.playcount == 0 and hosts_mode == '2' and not xbmc.getCondVisibility('system.platform.windows'):
                control.execute('Container.Refresh')
            elif self.playcount == 0 and hosts_mode == '1':
                control.execute('Container.Refresh')
            if self.isdebug: log_utils.log(__name__ + ' - parentDir: done (hosts_mode=%s)' % hosts_mode, log_utils.LOGINFO)
        except Exception as e:
            log_utils.log(__name__ + ' - parentDir error: %s' % e, log_utils.LOGERROR)


    def _wait_for_content(self, target, monitor, timeout=3.0):
        """Pollt Container.Content bis Zielwert erreicht oder Timeout.
        100ms-Intervall: schnell genug für reaktive UI, schonend genug für CPU.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if monitor.abortRequested():
                return False
            if control.getInfoLabel("Container.Content") == target:
                return True
            monitor.waitForAbort(0.1)
        return False

# keine Einträge für bookmarks und files in die Kodi DB 'MyVideos116.db' anlegen bzw. sofort löschen
    def runVideoDB(self):
        idFile = self.getVideoDB()
        if idFile != self.fileID:
            self.removeVideoDB(idFile)

    def getVideoDB(self):
        dbcon = database.connect(self.videoDB)
        dbcur = dbcon.cursor()
        dbcur.execute("SELECT * FROM files")
        match = dbcur.fetchall()
        dbcon.close()
        if match and len(match) > 0: idFile = len(match)
        else: idFile = 0
        return idFile

    def removeVideoDB(self, idFile):
        dbcon = database.connect(self.videoDB)
        dbcur = dbcon.cursor()
        dbcur.execute("DELETE FROM files WHERE idFile = '%s'" % idFile) # in DB vorhandener Trigger löscht auch den bookmark
        dbcon.commit()
        dbcon.close()


class subtitles:
    def __init__(self, *args, **kwargs):
        from xbmcaddon import Addon
        __scriptname__ = "XBMC Subtitles Login"
        __version__ = Addon().getAddonInfo('version')  # Module version
        BASE_URL_XMLRPC = u"http://api.opensubtitles.org/xml-rpc"

        self.server = _xmlrpclib.ServerProxy(BASE_URL_XMLRPC, verbose=0)
        login = self.server.LogIn(Addon().getSetting('subtitles.os_user'), Addon().getSetting('subtitles.os_pass'), "en", "%s_v%s" % (__scriptname__.replace(" ", "_"), __version__))
        if login["status"] == "200 OK":
            self.osdb_token = login["token"]

    def get(self, name, imdb, season, episode):
        isdebug = True if control.getSetting('status.debug') == 'true' else False
        if isdebug: log_utils.log('Start - get subtitles', log_utils.LOGINFO)
        subtitlepath = control.translatePath('special://temp/temp/')
        if not os.path.exists(subtitlepath): os.mkdir(subtitlepath)

        # struktur = json.loads(xbmc.executeJSONRPC('{"jsonrpc":"2.0","method":"Settings.GetSettingValue", "params":{"setting":"subtitles.custompath"},"id":1}'))
        # if struktur["result"]["value"] != subtitlepath:
        #     xbmc.executeJSONRPC('{"jsonrpc":"2.0","method":"Settings.setSettingValue","id":1,"params":{"setting":"subtitles.custompath", "value": "%s"}}' % subtitlepath.replace('\\', '\\\\'))

        controlfile = control.translatePath('special://temp/temp/%s' % name.replace(' ','-'))+'.ifo'
        if not os.path.exists(controlfile):
            for f in os.listdir(subtitlepath):
                os.remove(os.path.join(subtitlepath, f))
            with open(controlfile, mode='a'):
                pass

        season = str(season)
        episode = str(episode)
        # import pydevd
        # pydevd.settrace('localhost', port=12345, stdoutToServer=True, stderrToServer=True)
        try:
            langDict = {'Afrikaans': 'afr', 'Albanian': 'alb', 'Arabic': 'ara', 'Armenian': 'arm', 'Basque': 'baq', 'Bengali': 'ben', 'Bosnian': 'bos', 'Breton': 'bre', 'Bulgarian': 'bul', 'Burmese': 'bur', 'Catalan': 'cat', 'Chinese': 'chi', 'Croatian': 'hrv', 'Czech': 'cze', 'Danish': 'dan', 'Dutch': 'dut', 'English': 'eng', 'Esperanto': 'epo', 'Estonian': 'est', 'Finnish': 'fin', 'French': 'fre', 'Galician': 'glg', 'Georgian': 'geo', 'German': 'ger', 'Greek': 'ell', 'Hebrew': 'heb', 'Hindi': 'hin', 'Hungarian': 'hun', 'Icelandic': 'ice', 'Indonesian': 'ind', 'Italian': 'ita', 'Japanese': 'jpn', 'Kazakh': 'kaz', 'Khmer': 'khm', 'Korean': 'kor', 'Latvian': 'lav', 'Lithuanian': 'lit', 'Luxembourgish': 'ltz', 'Macedonian': 'mac', 'Malay': 'may', 'Malayalam': 'mal', 'Manipuri': 'mni', 'Mongolian': 'mon', 'Montenegrin': 'mne', 'Norwegian': 'nor', 'Occitan': 'oci', 'Persian': 'per', 'Polish': 'pol', 'Portuguese': 'por,pob', 'Portuguese(Brazil)': 'pob,por', 'Romanian': 'rum', 'Russian': 'rus', 'Serbian': 'scc', 'Sinhalese': 'sin', 'Slovak': 'slo', 'Slovenian': 'slv', 'Spanish': 'spa', 'Swahili': 'swa', 'Swedish': 'swe', 'Syriac': 'syr', 'Tagalog': 'tgl', 'Tamil': 'tam', 'Telugu': 'tel', 'Thai': 'tha', 'Turkish': 'tur', 'Ukrainian': 'ukr', 'Urdu': 'urd'}
            codePageDict = {'ara': 'cp1256', 'ar': 'cp1256', 'ell': 'cp1253', 'el': 'cp1253', 'heb': 'cp1255', 'he': 'cp1255', 'tur': 'cp1254', 'tr': 'cp1254', 'rus': 'cp1251', 'ru': 'cp1251'}

            langs = []
            try:
                try: langs = langDict[control.getSetting('subtitles.lang.1')].split(',')
                except: langs.append(langDict[control.getSetting('subtitles.lang.1')])
            except:
                langs = ['ger']
            ## Einstellungen/Player/Sprache
            # bevorzugte Audiosprache - Sprache der Benutzeroberfläche
            xbmc.executeJSONRPC('{"jsonrpc":"2.0","method":"Settings.setSettingValue", "params":{"setting":"locale.audiolanguage", "value": "default"},"id":1}')
            # bevorzugte Untertitelsprache - Sprache der Benutzeroberfläche
            xbmc.executeJSONRPC('{"jsonrpc":"2.0","method":"Settings.setSettingValue", "params":{"setting":"locale.subtitlelanguage", "value": "default"},"id":1}')
            ## Einstellungen/Player/Untertitel
            xbmc.executeJSONRPC('{"jsonrpc":"2.0","method":"Settings.setSettingValue", "params":{"setting":"subtitles.languages", "value": ["%s"]},"id":1}' % (control.getSetting('subtitles.lang.1')))

            try: subLang = xbmc.Player().getSubtitles()
            except: subLang = ''
            if subLang == langs[0]: raise Exception()

            imdbid = re.sub(r'[^0-9]', '', imdb)
            if season == 'None' or episode == 'None':
                result = self.server.SearchSubtitles(self.osdb_token, [{'sublanguageid': langs[0], 'imdbid': imdbid}])['data']
            else:
                result = self.server.SearchSubtitles(self.osdb_token, [{'sublanguageid': langs[0], 'imdbid': imdbid, 'season': season, 'episode': episode}])['data']
            if result == []: raise Exception()

            if langs == ['ger']: # alles nur deutsch
                result = [i for i in result if i['SubSumCD'] == '1' and i['LanguageName'] == 'German' and i['ISO639'] == 'de' and i['SubLanguageID'] == 'ger']
            else:
                result = [i for i in result if i['SubSumCD'] == '1']

            result=sorted(result, key=lambda k: int(k['SubSize']), reverse=True)

            # filter = []
            # for userrank in ['OS Legend','Administrator','Translator','Platinum member','Gold member','Silver member', 'Bronze member','trusted','']:
            #     for i in result:
            #         if i['UserRank'] == userrank.lower():
            #             filter.append(i)

            count = 0
            for i in result:
                try: lang = xbmc.convertLanguage(i['SubLanguageID'], xbmc.ISO_639_1)
                except: lang = i['SubLanguageID']
                subtitle = os.path.join(subtitlepath, i['SubFileName'])
                if not os.path.exists(subtitle):
                    ZipDownloadID = i['ZipDownloadLink'].split('/')[-1]
                    ZipDownloadLink = 'https://dl.opensubtitles.org/en/download/sub/%s' % ZipDownloadID

                    import requests, zipfile
                    r = requests.get(ZipDownloadLink, timeout=(3, 5))
                    status = r.status_code
                    if status == 200:
                        zf = zipfile.ZipFile(_io(r.content))
                        content = ''
                        for name in zf.namelist():
                            if not name.endswith('.srt'): continue
                            content = zf.read(name)

                        codepage = codePageDict.get(lang, '')
                        if codepage and control.getSetting('subtitles.utf') == 'true':
                            try:
                                content_encoded = codecs.decode(content, codepage)
                                content = codecs.encode(content_encoded, 'utf-8')
                            except:
                                pass

                        output = open(subtitle, 'wb')
                        output.write(content)
                        output.close()
                    else:
                        if count == 0:
                            from xbmcvfs import copy
                            errorfile = translatePath('special://home/addons/plugin.video.nebelflix.hybrid/resources/error.connect.opensubtitles.org.srt')
                            subtitle = subtitlepath + 'error.connect.opensubtitles.org.srt'
                            copy(errorfile, subtitle)
                            xbmc.Player().setSubtitles(subtitle)
                            return False
                        else: break
                xbmc.Player().setSubtitles(subtitle)
                count += 1
                if count == 3: break

            return True
        except:
            return False



class bookmarks:
    def get(self, name):
        from resources.lib import bookmarkDB
        offset = '0'
        try:
            # if not control.getSetting('bookmarks') == 'true': raise Exception()
            idFile = hashlib.md5()
            for i in name:
                try:
                    idFile.update(str(i).encode('utf-8'))
                except:
                    idFile.update(str(i))
            idFile = str(idFile.hexdigest())

            match = bookmarkDB.get_query(idFile, 'bookmarks.pcl')
            if match:
                self.offset = str(match[1])
                if self.offset == '0': raise Exception()
                minutes, seconds = divmod(float(self.offset), 60)
                hours, minutes = divmod(minutes, 60)
                label = '%02d:%02d:%02d' % (hours, minutes, seconds)
                label = control.py2_encode("Fortsetzen ab : %s" % label)
                if control.getSetting('bookmarks.auto') == 'false':
                    try:
                        yes = control.dialog.contextmenu([label, "Vom Anfang abspielen", ])
                    except:
                        yes = control.yesnoDialog(label, '', '', str(name), "Fortsetzen",
                                                  "Vom Anfang abspielen")
                    if yes:
                        bookmarkDB.remove_query(idFile, 'bookmarks')
                        self.offset = '0'
                return self.offset
            else:
                return offset
        except Exception as e:
            return offset


    def remove(self, name):
        from resources.lib import bookmarkDB
        try:
            idFile = hashlib.md5()
            for i in name:
                try:
                    idFile.update(str(i).encode('utf-8'))
                except:
                    idFile.update(str(i))
            idFile = str(idFile.hexdigest())
            # if (currentTime / totalTime) >= .87:
            bookmarkDB.remove_query(idFile, 'bookmarks')
        except:
            pass

    def save(self, currentTime, name):
        from resources.lib import bookmarkDB
        try:
            if int(currentTime) > 180:
                timeInSeconds = str(currentTime)
                idFile = hashlib.md5()
                for i in name:
                    try:
                        idFile.update(str(i).encode('utf-8'))
                    except:
                        idFile.update(str(i))
                idFile = str(idFile.hexdigest())
                bookmarkDB.save_query(idFile, timeInSeconds, 'bookmarks')
        except:
            pass
