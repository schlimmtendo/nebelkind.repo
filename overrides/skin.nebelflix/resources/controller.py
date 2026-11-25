"""Nebelflix: one fixed random spotlight per category visit.

Nebelflix Hybrid owns all metadata and playback routes. Scrolling/focusing/monitoring
never replaces that selection. A trailer starts only after an explicit request.
Detail actions use an immutable clicked-item snapshot and full-screen playback.
"""
import json
import random
import base64
import re
import sys
import time
import threading
import sqlite3
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import xbmc
import xbmcgui
import xbmcvfs
import library
import revisions_updater
import load_timing
import hub_protocol


ROWS = tuple(range(600, 648))
SPOTLIGHT_ROWS = (650, 651)
WIN = xbmcgui.Window(10000)


class SkinPlayer(xbmc.Player):
    def onAVStarted(self):
        prop('PlaybackEnded', '')
        prop('ResumeFallbackRequested', '')
        if prop('PlaybackOwner')=='manual_preview':
            prop('ManualPreviewPlaying','true')
            try:fit_spotlight_video()
            except Exception as exc:log('Manual trailer view: '+type(exc).__name__)
            return
        if prop('PlaybackOwner') != 'auto' or prop('AutoOwned')!='true':
            return
        if (not prop('AutoToken') or not spotlight_visible()
                or prop('SelectedPage') != prop('Page')):
            log('Late/invisible spotlight playback rejected')
            self.stop()
            prop('AutoOwned', 'false'); prop('AutoPlaying', 'false')
            restore_spotlight_view()
            return
        prop('AutoPlaying', 'true')
        try:
            audio_streams = self.getAvailableAudioStreams()
            if audio_streams:
                self.setAudioStream(0)
                log('Spotlight audio track selected: 0 (%s)' % audio_streams[0])
            else:
                log('Spotlight source reports no audio tracks', xbmc.LOGWARNING)
        except Exception as exc:
            log('Spotlight audio-track selection failed: ' + str(exc), xbmc.LOGWARNING)
        try:
            fit_spotlight_video()
            log('Spotlight video visible: ' + prop('Title'))
        except Exception as exc:
            log('Spotlight video fit: ' + str(exc))

    def onPlayBackStopped(self):
        if prop('PlaybackOwner')=='auto':
            prop('AutoOwned','false');prop('AutoPlaying','false');prop('AutoToken','');prop('PlaybackOwner','')
            restore_spotlight_view()
        if prop('PlaybackOwner') == 'manual':
            prop('RestorePlaybackFocus', 'true')

    def onPlayBackEnded(self):
        self.onPlayBackStopped()
        # The progress monitor removes the finished title and its temporary
        # stream URL on its next tick. Manual stops keep their resume point.
        prop('PlaybackEnded', 'true')
        raw = prop('ResumeFallback')
        if raw:
            try:
                fallback = json.loads(raw)
                remaining = float(fallback.get('Total') or 0) - float(fallback.get('Resume') or 0)
                started = float(prop('ResumeFallbackAt') or 0)
                if started and time.time() - started < 90 and remaining > 600:
                    prop('ResumeFallbackRequested', 'true')
                    log('Saved host stream ended early; resolving a fresh source for ' +
                        fallback.get('Title', ''), xbmc.LOGWARNING)
            except (TypeError, ValueError):
                pass

    def onPlayBackError(self):
        if prop('ResumeFallback'):
            prop('ResumeFallbackRequested', 'true')


PLAYER = SkinPlayer()
MONITOR = xbmc.Monitor()
FIELDS = hub_protocol.METADATA_FIELDS


def log(message, level=xbmc.LOGINFO):
    xbmc.log('[Nebelflix] ' + str(message), level)


def rpc(method, params=None):
    started = time.monotonic()
    response = json.loads(xbmc.executeJSONRPC(json.dumps({
        'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})))
    load_timing.record('skin', 'rpc.' + method, started, status='error' if 'error' in response else 'ok')
    if 'error' in response:
        raise RuntimeError(f'{method}: {response["error"]}')
    return response.get('result', {})


def prop(name, value=None):
    key = 'NF.' + name
    if value is None:
        return WIN.getProperty(key)
    if WIN.getProperty(key) != str(value):
        WIN.setProperty(key, str(value))


def publish_preload_progress(done, total):
    """Update both count and visible progress segments from the SAME total.

    The existing Kodi <progress><info> could display a near-zero bar even when
    PreloadCount read 72 / 101; labelled image segments avoid that limitation.
    """
    total = max(1, int(total))
    percent = max(0, min(100, int(100.0 * int(done) / total)))
    prop('PreloadCount', '%d / %d Reihen' % (int(done), total))
    prop('PreloadPercent', str(percent))
    for block in range(1, 21):
        prop('PreloadFill%02d' % block, 'true' if percent >= block * 5 else 'false')
    return percent


def helper_url(info_type, media_type, tmdb_id=None, **kwargs):
    params = {'info': info_type, 'tmdb_type': media_type, **kwargs}
    if tmdb_id:
        params['tmdb_id'] = tmdb_id
    return 'plugin://plugin.video.nebelflix.hybrid/?' + urlencode(params)


def open_directory(path, close_detail=False):
    if close_detail:
        xbmc.executebuiltin('Dialog.Close(1191)')
        # Let Kodi remove the modal detail dialog before activating Videos.
        xbmc.sleep(200)
    xbmc.executebuiltin(f'ActivateWindow(Videos,"{path}",return)')


def notification(text):
    xbmcgui.Dialog().notification('Nebelflix', text, time=4000)


def info(row, field, index=None):
    item = 'ListItem' if index is None else f'ListItemAbsolute({index})'
    return xbmc.getInfoLabel(f'Container({row}).{item}.{field}')


def selected_data(row, index=None):
    captured = info(row, 'Property(NF.Data)', index)
    if captured:
        return json.loads(captured)
    path = info(row, 'FileNameAndPath', index) or info(row, 'FolderPath', index)
    params = parse_qs(urlparse(path).query)
    # The clicked item's own route is authoritative. Never borrow a trailer or
    # identity from the global, asynchronously updated helper monitor.
    tmdb_id = ((params.get('tmdb_id') or [''])[0]
               or info(row, 'Property(tmdb_id)', index)
               or info(row, 'UniqueID(tmdb)', index))
    media_type = ((params.get('tmdb_type') or [''])[0]
                  or info(row, 'Property(tmdb_type)', index))
    dbtype = info(row, 'DBType', index)
    if not media_type:
        media_type = 'tv' if dbtype in ('tvshow', 'season', 'episode') else 'movie'
    if media_type == 'tvshow':
        media_type = 'tv'
    title = info(row, 'Title', index) or info(row, 'Label', index)
    if not title or title.lower() in ('next page', 'nächste seite', '..'):
        return {}
    return {
        'Title': title, 'Plot': info(row, 'Plot', index),
        'Year': info(row, 'Year', index), 'Rating': info(row, 'Rating', index),
        'Genres': info(row, 'Genre', index), 'Duration': info(row, 'Duration', index),
        'Fanart': (info(row, 'Art(fanart)', index) or info(row, 'Art(tvshow.fanart)', index)
                   or info(row, 'Art(landscape)', index) or info(row, 'Art(thumb)', index)),
        'Clearlogo': info(row, 'Art(clearlogo)', index) or info(row, 'Art(tvshow.clearlogo)', index),
        'Path': path, 'TMDB': tmdb_id if tmdb_id.isdigit() else '',
        'Type': media_type, 'DBType': dbtype,
        'Season': info(row, 'Season', index), 'Episode': info(row, 'Episode', index),
    }


def publish(data, prefix='NF'):
    data=dict(data)
    if str(data.get('Fanart','')).startswith('https://image.tmdb.org/t/p/'):
        profile=library.performance.PROFILES.get(prop('PerformanceProfile'),library.performance.PROFILES['balanced'])
        data['Fanart']=re.sub(r'(/t/p/)(?:w\d+|original)/',r'\g<1>'+profile['fanart']+'/',data['Fanart'])
    # Publish the atomic snapshot first; actions never assemble an item from
    # individual properties that the monitor may be replacing concurrently.
    WIN.setProperty(prefix + '.Item', json.dumps(data, ensure_ascii=False))
    for field in FIELDS:
        WIN.setProperty(prefix + '.' + field, str(data.get(field) or ''))
    if data.get('DBType') == 'channel':
        meta = [data.get('Genres'), data.get('EPG')]
    else:
        meta = [('Serie' if data.get('Type') == 'tv' else 'Film') if data.get('TMDB') or data.get('DBType') in ('movie','episode','tvshow') else '',
                data.get('Year'), data.get('Genres')]
    if data.get('Duration'):
        duration = str(data['Duration'])
        meta.append('%d Min.' % (int(duration)//60) if duration.isdigit() else duration)
    WIN.setProperty(prefix + '.Meta', '  ·  '.join(str(x) for x in meta if x))


def action_item(context='spotlight'):
    key = 'NFD.Item' if context == 'detail' else 'NF.Item'
    raw = WIN.getProperty(key)
    return json.loads(raw) if raw else {}


def remember_detail_origin(args):
    prop('DetailOrigin','')
    if not args or not args[0].isdigit():return
    row=int(args[0])
    if not 600<=row<648:return
    window=xbmcgui.Window(xbmcgui.getCurrentWindowId())
    try:position=max(0,int(args[1])-1) if len(args)>1 and args[1].isdigit() else window.getControl(row).getSelectedPosition()
    except RuntimeError:position=0
    prop('DetailOrigin',json.dumps(dict(window=xbmcgui.getCurrentWindowId(),row=row,position=position,page=window.getProperty('NF.Page'))))


def show_details(args):
    cancel_spotlight_video(suppress=True)
    if args and args[0].isdigit():
        row = int(args[0])
        if row not in ROWS:
            raise ValueError('Invalid content row')
        index = max(0, int(args[1])-1) if len(args)>1 and args[1].isdigit() else None
        if len(args)>2 and args[2] != prop('Page'):
            notification('Die Kategorie hat sich geändert. Bitte den Titel erneut auswählen.')
            return
        data = selected_data(row, index)
    else:
        data = action_item()
    if data.get('DirectNavigate') or data.get('DirectBack'):
        prop('DirectNavigate', json.dumps(data))
        return
    if data.get('Next') or data.get('Back'):
        row_id = data.get('Row', row if args and args[0].isdigit() else 0)
        prop('WidgetRequest', json.dumps({'row': row_id,
              'generation': data.get('Generation', ''),
              'retry': data.get('Retry', False), 'back': data.get('Back', False)}))
        return
    if data.get('Empty'):
        return
    if data.get('GuideOpen'):
        if not xbmc.getCondVisibility('System.HasAddon(plugin.video.nebelflix.tv)'):
            notification('Nebelflix TV bitte zuerst als eigenes Add-on installieren.')
            return
        cancel_spotlight_video(suppress=True)
        xbmc.executebuiltin('RunPlugin(plugin://plugin.video.nebelflix.tv/?action=guide)')
        return
    if data.get('AddonBrowse'):
        target = str(data.get('Path') or '')
        # Explicit whitelist: never execute a playlist-provided arbitrary URI.
        prefix = 'plugin://plugin.video.nebelflix.tv/?action=list&group='
        tv_allowed = (target.startswith(prefix) and
                (target[len(prefix):] in ('recent', 'favorites') or
                 re.fullmatch(r'vavoo_[a-z0-9_-]{1,80}',target[len(prefix):]) is not None))
        if not tv_allowed:
            notification('Dieser Live-TV-Bereich ist nicht freigegeben.')
            return
        required = 'plugin.video.nebelflix.tv'
        if not xbmc.getCondVisibility('System.HasAddon(%s)' % required):
            notification('Bitte %s installieren und aktivieren.' % required)
            return
        cancel_spotlight_video(suppress=True)
        return open_directory(target)
    if data.get('AddonLauncher'):
        addon_id = str(data.get('AddonID') or '')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', addon_id):
            notification('Dieses Add-on kann nicht gestartet werden.')
            log('Blocked invalid add-on ID in launcher: ' + addon_id, xbmc.LOGWARNING)
            return
        cancel_spotlight_video(suppress=True)
        xbmc.executebuiltin('RunAddon(%s)' % addon_id)
        return
    # Vavoo groups remain in the normal home carousel. Selecting a channel
    # opens its now/next details; playback is an explicit button action.
    if data.get('DBType') == 'channel':
        publish(data, 'NFD')
        WIN.setProperty('NFD.MyListLabel', '')
        WIN.setProperty('NFD.Token','')
        WIN.setProperty('NFD.Upcoming','')
        WIN.setProperty('NFD.Tagline','')
        WIN.setProperty('NFD.Details',data.get('EPG') or 'Live-TV')
        WIN.setProperty('NFD.DirectorID','')
        WIN.setProperty('NFD.CollectionID','')
        WIN.setProperty('NFD.Stars','')
        WIN.setProperty('NFD.RatingLabel','')
        WIN.setProperty('NFD.CastReady','true')
        WIN.setProperty('NFD.Actor1.Name','')
        remember_detail_origin(args)
        prop('DetailOpen', 'true')
        xbmc.executebuiltin('ActivateWindow(1191)')
        return
    if (data.get('Folder') and data.get('Path', '').startswith(
            'plugin://skin.nebelflix/?vavoo=group')):
        filename = (parse_qs(urlparse(data['Path']).query).get('file') or [''])[0]
        if filename:
            browse_path = 'nf://vavoo/group?' + urlencode({'file': filename})
            prop('BrowseWidget', json.dumps({'row': row, 'path': browse_path}))
        return
    # Kodi's native TV Guide understands PVR channel group paths. Open that
    # guide directly instead of browsing the group as an ordinary folder.
    if data.get('Folder') and data.get('Path', '').startswith('pvr://channels/tv/'):
        cancel_spotlight_video(suppress=True)
        xbmc.executebuiltin('ActivateWindow(TVGuide,"%s")' % data['Path'].replace('"', ''))
        return
    if data.get('Folder') and not data.get('TMDB'):
        prop('BrowseWidget', json.dumps({'row': row, 'path': data['Path'], 'back': data.get('Back', False)}))
        return
    if data.get('Channel') or data.get('Path', '').startswith('pvr://'):
        return play_selected(data)
    if not data.get('Title'):
        notification('Der ausgewählte Titel wird noch geladen.')
        return
    publish(data, 'NFD')
    detail_token=str(time.time_ns())
    WIN.setProperty('NFD.Token',detail_token)
    WIN.setProperty('NFD.Section','plot')
    WIN.setProperty('NFD.Details','Zusätzliche Informationen werden geladen …')
    WIN.setProperty('NFD.Cast','Besetzung wird geladen …')
    WIN.setProperty('NFD.Tagline','')
    WIN.setProperty('NFD.DirectorID','')
    WIN.setProperty('NFD.CollectionID','')
    WIN.setProperty('NFD.Stars','')
    WIN.setProperty('NFD.RatingLabel','')
    WIN.setProperty('NFD.CastReady','')
    for index in range(1,21):
        for field in ('Name','Role','ID','Image'):WIN.setProperty('NFD.Actor%s.%s'%(index,field),'')
    WIN.setProperty('NFD.Upcoming','true' if data.get('Upcoming') else '')
    WIN.setProperty('NFD.MyListLabel', detail_list_label(data))
    remember_detail_origin(args)
    prop('DetailOpen', 'true')
    log(f'Detail snapshot: {data["Title"]} [{data.get("Type")}:{data.get("TMDB")}]')
    xbmc.executebuiltin('ActivateWindow(1191)')
    threading.Thread(target=enrich_details,args=(dict(data),detail_token),daemon=False).start()


def enrich_details(item,token):
    try:
        import detail_metadata
        values=detail_metadata.fetch(item)
        if WIN.getProperty('NFD.Token')!=token or prop('DetailOpen')!='true':return
        for key,value in values.items():WIN.setProperty('NFD.'+key,str(value))
        if not values:raise ValueError('No detail metadata')
    except Exception as exc:
        if WIN.getProperty('NFD.Token')==token:
            WIN.setProperty('NFD.Details','Zusätzliche Informationen sind derzeit nicht verfügbar.')
            WIN.setProperty('NFD.Cast','Besetzung ist derzeit nicht verfügbar.')
            WIN.setProperty('NFD.CastReady','true')
        log('Detail metadata: '+type(exc).__name__)


def resolve_trailer(item):
    import trailers
    try:
        result = trailers.resolve(item)
        log('MultiTrailer lookup: %s:%s found=%s' %
            (item.get('Type'), item.get('TMDB'), bool(result)))
        return result
    except Exception as exc:
        log('MultiTrailer unavailable: ' + str(exc))
        return None


def play_trailer(item, context='spotlight'):
    if (item.get('Channel') or item.get('DBType') == 'channel'
            or item.get('Path', '').startswith('pvr://') or not item.get('TMDB')):
        notification('Trailer gibt es nur für Film- und Serieninhalte.')
        return
    cancel_spotlight_video(suppress=True)
    prop('ManualTrailerRequest',json.dumps(dict(item=dict(item),context=context,page=prop('Page'))))
    prop('ManualTrailerBusy','true')


def play_selected(item):
    import release_policy
    if item.get('Upcoming') or (release_policy.release_date(item) and not release_policy.visible(item)):
        notification('Dieser Titel ist noch nicht erschienen. Trailer und Merkliste sind verfügbar.')
        return
    cancel_spotlight_video(suppress=True)
    prop('PlaybackOwner', 'manual')
    if item.get('DBType') == 'channel':
        xbmc.executebuiltin('Dialog.Close(1191)')
        return play_path(item)
    # Opening a series from its detail card browses its seasons; it is not yet
    # a playback request and must not leave a resume snapshot for an episode.
    if item.get('Type') == 'tv' and item.get('DBType') != 'episode':
        return play_tmdb_helper(item, close_detail=True)
    item = attach_saved_resume(item)
    # A title the user chose to watch again should become eligible for
    # Continue Watching, even if it had previously been dismissed there.
    library.restore_continue(item)
    if item.get('Resume'):
        choice = xbmcgui.Dialog().select(item.get('Title', 'Weiterschauen'),
            ['Fortsetzen ab %d:%02d' % (int(item['Resume'])//60, int(item['Resume'])%60), 'Von Anfang an'])
        if choice < 0:
            return
        item = dict(item, Resume=item['Resume'] if choice == 0 else 0)
    publish_playback_info(item)
    item = library.resume_record(item)
    prop('PendingPlay', json.dumps(dict(item, RequestedAt=time.time())))
    xbmc.executebuiltin('Dialog.Close(1191)')
    return play_tmdb_helper(item) if item.get('TMDB') and item.get('Type') in ('movie', 'tv') else play_path(item)


def publish_playback_info(item):
    """Keep the selected TMDB metadata visible while a hoster resolves/starts."""
    item = dict(item)
    try:
        detail = json.loads(WIN.getProperty('NFD.Item') or '{}')
        if item.get('TMDB') and detail.get('TMDB') == item['TMDB']:
            for key in ('Title', 'ShowTitle', 'Plot', 'Year', 'Rating', 'Genres',
                        'Season', 'Episode', 'DBType', 'Duration', 'Fanart',
                        'Poster', 'Clearlogo'):
                if not item.get(key) and detail.get(key):
                    item[key] = detail[key]
    except (TypeError, ValueError):
        pass
    for key in ('Title', 'ShowTitle', 'Plot', 'Year', 'Genres', 'Season', 'Episode',
                'DBType', 'Duration', 'Fanart', 'Poster', 'Clearlogo'):
        prop('Playback' + key, item.get(key, ''))
    rating = item.get('Rating', '')
    try:
        rating = '%.1f' % float(rating) if rating else ''
    except (TypeError, ValueError):
        rating = str(rating)
    prop('PlaybackRating', rating)


def attach_saved_resume(item):
    """Retrieve the exact resume position from Hybrid, never progress.json."""
    if item.get('Resume'):
        return item
    import history_access
    entry = history_access.resume_for(item)
    if not entry or entry.get('completed') or not entry.get('position'):
        return item
    return dict(item, Resume=entry['position'], Total=entry.get('duration') or 0)


def saved_stream_is_fresh(url):
    if not library.is_direct_media_source(url):
        return False
    query = parse_qs(urlparse(str(url).split('|', 1)[0]).query)
    expires = next((query[key][0] for key in ('expires', 'expiry', 'exp')
                    if query.get(key)), '')
    if not expires:
        # Some providers issue opaque media URLs without an expiry parameter.
        # Try the saved source first for immediate Continue playback; the
        # existing playback-error callback falls back to Nebelflix Hybrid if stale.
        return True
    try:
        expiry = float(expires)
        if expiry > 100000000000:
            expiry /= 1000.0
        return expiry > time.time() + 60
    except ValueError:
        return False


def play_tmdb_helper(item, close_detail=False):
    if item.get('Type') == 'tv' and item.get('DBType') != 'episode':
        path = helper_url('seasons', 'tv', item.get('TMDB'))
        log('Opening Nebelflix Hybrid seasons: ' + item.get('Title', '') + ' [' + item.get('TMDB', '') + ']')
        return open_directory(path, close_detail=close_detail)
    params = {'info': 'play', 'tmdb_type': item['Type'], 'tmdb_id': item['TMDB']}
    if item.get('DBType') == 'episode':
        for key in ('Season', 'Episode'):
            if str(item.get(key, '')).isdigit():
                params[key.lower()] = item[key]
    return PLAYER.play('plugin://plugin.video.nebelflix.hybrid/?' + urlencode(params))



def play_path(item):
    if item.get('Path'):
        if library.is_direct_media_source(item['Path']) and not saved_stream_is_fresh(item['Path']):
            notification('Die gespeicherte Videoquelle ist abgelaufen und hat keine TMDB-ID zur erneuten Suche.')
            return
        return PLAYER.play(item['Path'])
    return open_directory('plugin://plugin.video.nebelflix.hybrid/')


def resume_helper_fallback():
    raw = prop('ResumeFallback')
    prop('ResumeFallbackRequested', '')
    prop('ResumeFallback', '')
    prop('ResumeFallbackAt', '')
    if not raw:
        return
    try:
        item = json.loads(raw)
    except Exception as exc:
        log('Invalid saved-stream fallback: ' + str(exc), xbmc.LOGERROR)
        return
    if item.get('TMDB') and item.get('Type') in ('movie', 'tv'):
        # This signed manifest demonstrably failed. Keep its resume point but
        # discard the dead URL so the next attempt resolves a fresh provider link.
        # No second skin history file; Hybrid will resolve a fresh stream.
        log('Saved stream failed; resolving again through Nebelflix Hybrid: ' + item.get('Title', ''))
        return play_tmdb_helper(item)
    notification('Der Streamlink ist abgelaufen. Bitte den Titel erneut aus seiner Quelle öffnen.')


def mylist_matches(item, favourites=None, saved=None):
    """Look up a Nebelflix bookmark in the one authoritative private list.

    `favourites` is kept in the signature for compatibility with older skin
    callers, but Kodi's independent Favourites database is not modified.
    """
    saved = library.read_mylist(rpc) if saved is None else saved
    key = library.mylist_identity(item)
    local = next((entry for entry in saved
                  if key and library.mylist_identity(entry) == key), None)
    return local, []


def detail_list_label(item):
    try:
        local, _ = mylist_matches(item)
        return '− Entfernen' if local else '+ Meine Liste'
    except Exception as exc:
        log('Meine-Liste-Status konnte nicht gelesen werden: ' + str(exc), xbmc.LOGWARNING)
        return '+ Meine Liste'


def mylist(item):
    """Add or remove ONLY this exact film/series in Nebelflix's local list.

    Hybrid playback history, Kodi favourites, and widget configuration are
    unrelated. Do not call Kodi Favourites.AddFavourite or edit favourites.xml;
    those are separate user data stores with their own caching and lifetime.
    """
    if not item.get('Title'):
        notification('Dieser Titel kann nicht gespeichert werden.')
        return False
    key = library.mylist_identity(item)
    if not key:
        notification('Keine eindeutige Titelkennung vorhanden.')
        return False
    try:
        saved = library.read_mylist(rpc)
        present = any(library.mylist_identity(entry) == key for entry in saved)
        if present:
            saved = [entry for entry in saved
                     if library.mylist_identity(entry) != key]
        else:
            path = (helper_url('details', item['Type'], item['TMDB'])
                    if item.get('TMDB') and item.get('Type') in ('movie', 'tv')
                    else item.get('Path') or '')
            if not path:
                notification('Der Titel besitzt keinen gültigen Aufrufpfad.')
                return False
            saved.insert(0, dict(item, Path=path))
        # Atomic JSON write; an exception must not show a false success banner.
        library.write('mylist.json', saved)
    except Exception as exc:
        log('Meine Liste konnte nicht gespeichert werden: ' + str(exc), xbmc.LOGERROR)
        notification('Meine Liste konnte nicht gespeichert werden.')
        return False
    WIN.setProperty('NFD.MyListLabel', '+ Meine Liste' if present else '− Entfernen')
    prop('RefreshWidgets', str(time.time_ns()))
    notification('Aus Meine Liste entfernt' if present else 'Zu Meine Liste hinzugefügt')
    return True


def stop_intro():
    owned = prop('IntroActive') == 'true'
    prop('IntroActive', 'false')
    if owned:
        PLAYER.stop()


def intro():
    cancel_spotlight_video(suppress=True)
    prop('PlaybackOwner', 'intro')
    stop_intro()
    xbmc.executebuiltin('Dialog.Close(1190)')
    if xbmcgui.getCurrentWindowId() != 10000:
        xbmc.executebuiltin('ActivateWindow(Home)')
    clip = xbmcvfs.translatePath('special://skin/media/nebelflix/intro.mp4')
    if not xbmcvfs.exists(clip) or PLAYER.isPlaying():
        return
    prop('IntroActive', 'true'); prop('SkipIntro', 'false')
    PLAYER.play(clip, windowed=True)
    started = time.monotonic()
    while not MONITOR.waitForAbort(0.1) and time.monotonic()-started < 8:
        if prop('SkipIntro') == 'true':
            break
        if time.monotonic()-started > 1 and not PLAYER.isPlaying():
            break
        try:
            WIN.setFocusId(1999)
        except RuntimeError:
            pass
    stop_intro()
    try:
        WIN.setFocusId(100)
    except RuntimeError:
        pass
    log('Intro completed with embedded logo audio')


def spotlight_candidates(page):
    if prop('DirectHubs') == 'true':
        candidates = []
        for row in ROWS:
            count = int(xbmc.getInfoLabel('Container(%s).NumItems' % row) or 0)
            for position in range(min(count, 10)):
                data = selected_data(row, position)
                if data.get('TMDB') and data.get('Type') in ('movie', 'tv'):
                    candidates.append((row, position))
            if candidates:
                break
        return candidates
    counts = {row: int(xbmc.getInfoLabel(f'Container({row}).NumItems') or 0)
              for row in SPOTLIGHT_ROWS}
    if page in ('movie', 'tv'):
        source_row = 650 if page == 'movie' else 651
        candidates = [(source_row, index) for index in range(counts[source_row])]
    else:
        candidates = [(row, index) for index in range(max(counts.values()))
                      for row in SPOTLIGHT_ROWS if index < counts[row]]
    # Helper may append a "Mehr …" navigation folder even with content limits.
    # Only real title identities may participate in the random spotlight.
    valid = []
    for row, index in candidates:
        path = info(row, 'FileNameAndPath', index) or info(row, 'FolderPath', index)
        params = parse_qs(urlparse(path).query)
        identity = (params.get('tmdb_id') or [''])[0] or info(row, 'Property(tmdb_id)', index)
        if identity.isdigit():
            valid.append((row, index))
    return valid


def update_spotlight(page):
    if xbmc.getCondVisibility('Skin.HasSetting(NFHideSpotlight)'):return {}
    if prop('SelectedPage') == page:
        return action_item()  # Freeze identity/art until a different tab is visited.
    candidates = spotlight_candidates(page)
    if not candidates:
        return
    pool = []
    for row, position in candidates:
        data = selected_data(row, position)
        expected = 'tv' if page == 'tv' or row == 651 else 'movie'
        if data.get('Type') == expected and data.get('TMDB'):
            pool.append(data)
    if not pool:
        return  # Providers may briefly retain data from the previous category.
    previous = prop('LastSpotlightIdentity.' + page)
    recent_key = 'RecentSpotlightIdentities.' + page
    try:
        recent = json.loads(prop(recent_key) or '[]')
    except (TypeError, ValueError):
        recent = []
    recent = [str(value) for value in recent if value]
    choices = [data for data in pool if item_identity(data) not in recent[-6:]]
    if not choices:
        choices = [data for data in pool if item_identity(data) != previous] or pool
    data = random.choice(choices or pool)
    publish(data)
    prop('SelectedPage', page)
    prop('LastSpotlightIdentity.' + page, item_identity(data))
    recent = (recent + [item_identity(data)])[-8:]
    prop(recent_key, json.dumps(recent))
    prop('SpotlightCaption', 'SERIEN FÜR DICH' if page == 'tv' else
         'FILME FÜR DICH' if page == 'movie' else 'FÜR DICH · FILME & SERIEN')
    log(f'Spotlight selected: {page} {data["Title"]} [{item_identity(data)}]')
    return data


def item_identity(item):
    return item.get('Type', '') + ':' + item.get('TMDB', '')


def spotlight_visible():
    if xbmc.getCondVisibility('Skin.HasSetting(NFHideSpotlight)'):return False
    return (prop('HasHero') == 'true' and (xbmcgui.getCurrentWindowId() == 10000
            or xbmc.getCondVisibility('!String.IsEmpty(Window.Property(NF.HubSlot))'))
            and not xbmc.getCondVisibility('Window.IsVisible(1191)')
            and not any(xbmc.getCondVisibility(f'Control.HasFocus({row})') for row in ROWS))


def restore_spotlight_view():
    saved = prop('AutoOriginalView')
    WIN.clearProperty('NF.AutoOriginalView')
    if saved:
        try:
            rpc('Player.SetViewMode', {'viewmode': json.loads(saved)})
        except Exception as exc:
            log('Restore spotlight view: ' + str(exc))


def cancel_spotlight_video(suppress=False):
    # Invalidate lookups before stopping playback: late results cannot start.
    prop('AutoToken', '')
    if suppress:
        prop('AutoSuppressed', 'true')
    owned = prop('AutoOwned') == 'true'
    prop('AutoOwned', 'false'); prop('AutoPlaying', 'false')
    if owned:
        PLAYER.stop()
    restore_spotlight_view()


def fit_spotlight_video():
    if prop('AutoOriginalView'):
        return
    prop('AutoOriginalView', json.dumps(rpc('Player.GetViewMode')))
    try:
        aspect = float(xbmc.getInfoLabel('VideoPlayer.VideoAspect') or 16/9)
    except ValueError:
        aspect = 16/9
    target = 1792/800
    zoom = min(2.0, max(target/aspect, aspect/target) * 1.02 * (1.12 if aspect <= 1.9 else 1))
    rpc('Player.SetViewMode', {'viewmode': {'zoom': zoom, 'pixelratio': 1.0,
        'verticalshift': 0.0, 'nonlinearstretch': False}})
    prop('AutoPlaying', 'true')


class SpotlightVideo:
    """Resolve asynchronously, but authorize and start only on the monitor thread."""
    def __init__(self):
        self.token = ''
        self.result = None
        self.started = False
        self.requested_at = 0
        self.dwell = 0
        self.automatic = False

    def reset(self):
        # This automaton must never stop a manually requested trailer.
        if self.token and prop('AutoToken') == self.token:
            cancel_spotlight_video()
        self.token = ''; self.result = None; self.started = False; self.dwell = 0

    def lookup(self, token, item):
        try:
            trailer = resolve_trailer(item)
        except Exception as exc:
            trailer = ''; log('Spotlight trailer unavailable: ' + str(exc))
        if token == self.token and token == prop('AutoToken'):
            self.result = trailer or ''

    def tick(self, page, item):
        request = prop('TrailerRequest')
        if request:
            prop('TrailerRequest', '')
        disabled = (xbmc.getCondVisibility('Skin.HasSetting(NFDisableAutoTrailer)')
                    or prop('PerformanceProfile') == 'low'
                    or prop('ManualTrailerBusy') == 'true' or prop('ManualTrailerActive') == 'true')
        if disabled:
            self.dwell = 0
        if disabled and self.automatic and self.token:
            self.reset()
        if (not spotlight_visible() or prop('Page') != page):
            self.dwell = 0
            if self.token:
                self.reset()
            return
        if self.token and prop('AutoToken') != self.token:
            self.reset()
        automatic = False
        if (not request and not disabled and not self.token and item.get('TMDB')
                and prop('AutoAttempted') != 'true' and prop('AutoSuppressed') != 'true'
                and not PLAYER.isPlaying()):
            if not self.dwell:
                self.dwell = time.monotonic()
            if time.monotonic() - self.dwell >= 3:
                request = json.dumps(item)
                automatic = True
        if request:
            requested = json.loads(request)
            if item_identity(requested) != item_identity(item) or PLAYER.isPlaying():
                return
            self.reset()
            self.automatic = automatic
            self.token = f'{time.monotonic_ns()}:{page}:{item_identity(item)}'
            prop('AutoToken', self.token); prop('AutoAttempted', 'true')
            self.requested_at = time.monotonic()
            threading.Thread(target=self.lookup, args=(self.token, dict(item)), daemon=True).start()
        if not self.started and self.result is not None:
            self.started = True
            if self.result and not PLAYER.isPlaying() and prop('AutoToken') == self.token:
                prop('AutoOwned', 'true')
                prop('PlaybackOwner', 'auto')
                self.requested_at = time.monotonic()
                log(f'Spotlight requested trailer: {item["Title"]} [{item_identity(item)}]')
                import trailers
                trailers.play(PLAYER, self.result, windowed=True)
            elif not self.result and not self.automatic:
                notification('Für diesen Titel ist kein deutscher oder englischer Trailer verfügbar.')
        if self.started and prop('AutoOwned') == 'true':
            if PLAYER.isPlayingVideo():
                try:
                    fit_spotlight_video()
                except Exception as exc:
                    log('Spotlight fit: ' + str(exc))
            elif prop('AutoPlaying') == 'true':
                self.reset()  # Ended/failed: no repeat during this visit.


def diagnostic():
    raw = prop('DiagnosticSnapshot')
    report = json.loads(raw) if raw else {'skin': xbmc.getSkinDir(), 'autoplay': False}
    xbmcvfs.mkdirs('special://profile/addon_data/skin.nebelflix/')
    with xbmcvfs.File('special://profile/addon_data/skin.nebelflix/runtime-check.json', 'w') as stream:
        stream.write(json.dumps(report, indent=2, ensure_ascii=False))
    log('Runtime diagnostic: ' + json.dumps(report))
    notification('Laufzeitprüfung gespeichert')


def ensure_default_skin_settings():
    """Disable spotlight autoplay only when this profile has no saved choice."""
    settings_path = xbmcvfs.translatePath(
        'special://profile/addon_data/skin.nebelflix/settings.xml')
    if xbmcvfs.exists(settings_path):
        try:
            root = ET.parse(settings_path).getroot()
        except (OSError, ET.ParseError) as exc:
            log('Could not read saved Nebelflix settings; preserving them: ' + str(exc),
                xbmc.LOGWARNING)
            return
        if any(node.get('id', '').casefold() == 'nfdisableautotrailer'
               for node in root.findall('setting')):
            return
    xbmc.executebuiltin('Skin.SetBool(NFDisableAutoTrailer)')
    log('Initialized default: automatic spotlight trailers disabled')


def monitor():
    import hub_runtime
    return hub_runtime.monitor(sys.modules[__name__])





def manage_tv_epg():
    """Keep TV configuration in its owner add-on, not the obsolete PVR bridge."""
    if not xbmc.getCondVisibility('System.HasAddon(plugin.video.nebelflix.tv)'):
        xbmcgui.Dialog().ok('Nebelflix TV','Bitte Nebelflix TV installieren.')
        return
    xbmc.executebuiltin('RunPlugin(plugin://plugin.video.nebelflix.tv/?action=manage)')


def dispatch(command, args=()):
    if command != 'monitor' and prop('Revisions.Running') == 'true':
        log('Deferred action during revision installation: ' + command)
        return
    if command != 'monitor':
        log('Action: ' + command + ' ' + ','.join(args))
    context = args[0] if args and args[0] == 'detail' else 'spotlight'
    if command == 'monitor':
        return monitor()
    if command == 'setup':
        import hub_runtime
        if hub_runtime.setup(force=True):
            prop('LayoutChanged', str(time.time_ns()))
        return
    if command == 'setup_menu':
        choice = xbmcgui.Dialog().select('Einrichtung & Widgets',
            ['Reiter und Widgets verwalten', 'Setup-Assistent öffnen'])
        if choice == 0:
            return library.edit_layout(rpc, prop)
        if choice == 1:
            return dispatch('setup')
        return
    if command == 'stop':
        prop('StopController', 'true'); return
    if command == 'reload':
        cancel_spotlight_video(suppress=True)
        stop_intro(); prop('StopController', 'true'); MONITOR.waitForAbort(0.8)
        xbmc.executebuiltin('UpdateLocalAddons')
        MONITOR.waitForAbort(1)
        return xbmc.executebuiltin('ReloadSkin()')
    if command == 'skip_intro':
        prop('SkipIntro', 'true'); return
    if command == 'intro':
        return intro()
    if command == 'detail_collection':
        ident=WIN.getProperty('NFD.CollectionID')
        if not ident.isdigit():return
        route='plugin://plugin.video.nebelflix.hybrid/?'+urlencode(dict(info='collection',tmdb_type='movie',tmdb_id=ident))
        xbmc.executebuiltin('Dialog.Close(1191)')
        return open_directory(route)
    if command == 'detail_person':
        if not args or not str(args[0]).isdigit():return
        kind='with_crew' if len(args)>1 and args[1]=='director' else 'with_cast'
        route='plugin://plugin.video.nebelflix.hybrid/?'+urlencode(dict(info='discover',tmdb_type='movie',**{kind:args[0]}))
        xbmc.executebuiltin('Dialog.Close(1191)')
        return open_directory(route)
    if command == 'details':
        return show_details(args)
    if command == 'trailer':
        return play_trailer(action_item(context), context)
    if command == 'clear_widget_cache':
        if not xbmcgui.Dialog().yesno('Widget-Cache bereinigen',
                'Gespeicherte Widget-Inhalte, Vorschaubilder und Logos beim nächsten Kodi-Start löschen und neu laden? Wiedergabeverlauf und Einstellungen bleiben erhalten.'):
            return
        library.schedule_widget_cache_reset(include_continue=False)
        xbmcgui.Dialog().ok('Nebelflix', 'Beim nächsten Kodi-Neustart werden Widget-Inhalte, Bilder und Logos bereinigt und neu vorbereitet. Der Hybrid-Verlauf bleibt erhalten.')
        return
    if command == 'manage_tv':
        if xbmc.getCondVisibility('System.HasAddon(plugin.video.nebelflix.tv)'):
            xbmc.executebuiltin('RunPlugin(plugin://plugin.video.nebelflix.tv/?action=manage)')
        else:
            xbmcgui.Dialog().ok('Nebelflix TV',
                'Bitte zuerst das eigenständige Nebelflix-TV-Add-on installieren. '
                'Danach sind hier die EPG-Quellen, Senderlogos und M3U-Listen erreichbar.')
        return
    if command == 'reset_watched':
        xbmc.executebuiltin('RunPlugin(plugin://plugin.video.nebelflix.hybrid/?action=resetWatched)')
        return
    if command == 'reset_playback_status':
        xbmc.executebuiltin('RunPlugin(plugin://plugin.video.nebelflix.hybrid/?action=resetPlaybackStatus)')
        return
    if command == 'manage_history':
        # Hybrid owns both the history and old playback bookmarks.
        xbmc.executebuiltin('RunPlugin(plugin://plugin.video.nebelflix.hybrid/?action=historyManager)')
        return
    if command == 'skip_preload':
        prop('SkipPreload', 'true')
        return
    if command == 'edit_layout':
        return library.edit_layout(rpc, prop)
    if command == 'tab':
        page = prop('TabPage' + args[0])
        if page and page != prop('Page'):
            cancel_spotlight_video(suppress=True)
            prop('Page', page)
            prop('FocusAfterTab', '200')
        return
    if command == 'play':
        if (prop('PlaybackOwner') == 'tv'
                and (WIN.getProperty('NFTV.ReturnGuide') or WIN.getProperty('NFTV.RestoreGuide'))):
            return
        item = action_item(context)
        if not item.get('Path') and not item.get('TMDB'):
            return
        return play_selected(item)
    if command == 'mylist':
        return mylist(action_item(context))
    if command == 'remove_continue':
        if not args:
            return
        token = args[0]
        try:
            item = json.loads(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)).decode('utf-8'))
        except Exception as exc:
            log('Invalid continue removal item: ' + str(exc), xbmc.LOGERROR)
            return
        title = item.get('Title', 'Diesen Titel')
        if item.get('DBType') == 'episode':
            title = '%s · S%sE%s' % (title, item.get('Season', '?'), item.get('Episode', '?'))
        if not xbmcgui.Dialog().yesno('Aus Weiterschauen entfernen', '„%s“ aus Weiterschauen entfernen?' % title):
            return
        if library.dismiss_continue(item):
            prop('RefreshWidgets', str(time.time_ns()))
            notification('Fortsetzungspunkt in Hybrid entfernt')
        else:
            notification('Eintrag konnte nicht bestätigt entfernt werden')
        return
    if command == 'browse':
        item = action_item(context)
        path = (helper_url('seasons' if item.get('Type') == 'tv' else 'details',
                           item.get('Type'), item.get('TMDB'))
                if item.get('TMDB') else 'plugin://plugin.video.nebelflix.hybrid/')
        return open_directory(path, close_detail=True)
    if command == 'search':
        # Film tab searches films, Series tab searches series, all other tabs search both.
        # An explicit kind is used by the skin's optional search selection dialog.
        explicit = args[0] if args and args[0] in ('movie', 'tv', 'both') else ''
        media = explicit or {'movie': 'movie', 'tv': 'tv'}.get(prop('Page'), 'both')
        heading = {'movie': 'Nebelflix Hybrid – Filme suchen',
                   'tv': 'Nebelflix Hybrid – Serien suchen',
                   'both': 'Nebelflix Hybrid – Filme und Serien suchen'}[media]
        keyboard = xbmc.Keyboard('', heading)
        keyboard.doModal()
        if keyboard.isConfirmed():
            query = keyboard.getText().strip()
            if query:
                return open_directory(helper_url('search', media, query=query))
        return
    if command == 'diagnostic':
        return diagnostic()
    if command == 'timing_debug':
        choice = xbmcgui.Dialog().select('Ladezeiten-Diagnose',
            ['Diagnose einschalten', 'Diagnose ausschalten', 'Messbericht anzeigen'])
        if choice in (0, 1):
            library.write('diagnostics.json', {'enabled': choice == 0})
            prop('DebugTiming', 'true' if choice == 0 else 'false')
            notification('Ladezeiten-Diagnose ' + ('aktiv' if choice == 0 else 'ausgeschaltet'))
        elif choice == 2:
            xbmcgui.Dialog().textviewer('Ladezeiten: Durchschnitt und Maximum', load_timing.report())
        return
    raise ValueError('Unknown Nebelflix command: ' + command)


if __name__ == '__main__':
    try:
        dispatch(sys.argv[1] if len(sys.argv)>1 else 'monitor', sys.argv[2:])
    except Exception as exc:
        log(f'{type(exc).__name__}: {exc}', xbmc.LOGERROR)
        if len(sys.argv)>1 and sys.argv[1] != 'monitor':
            notification('Aktion fehlgeschlagen. Details stehen im Kodi-Protokoll.')
        raise
