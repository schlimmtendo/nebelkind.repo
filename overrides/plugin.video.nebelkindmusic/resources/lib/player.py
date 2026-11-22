from urllib.parse import urlencode
import xbmc
import xbmcgui
from resources.lib.catalog import PLAYLIST_ID


def playlist_url(playlist_id, browse=False):
    if not PLAYLIST_ID.fullmatch(str(playlist_id or '')):
        raise ValueError('Keine normale öffentliche YouTube-Playlist')
    if browse:
        return 'plugin://plugin.video.youtube/playlist/' + playlist_id + '/'
    return 'plugin://plugin.video.youtube/play/?' + urlencode(
        {'playlist_id': playlist_id, 'action': 'play'})


def track_cache():
    import xbmcaddon,xbmcvfs
    from pathlib import Path
    addon=xbmcaddon.Addon('plugin.video.nebelkindmusic')
    return Path(xbmcvfs.translatePath(addon.getAddonInfo('profile'))) / 'playlist-tracks'


def video_url(video_id):
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',str(video_id)):raise ValueError('Ungültiger Titel')
    return 'plugin://plugin.video.youtube/play/?'+urlencode({'video_id':video_id})


def play_playlist(playlist_id):
    from resources.lib.catalog import load_tracks
    try:
        result=load_tracks(playlist_id,track_cache())
        tracks=result['tracks']
        queue=xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        # Resolve the public track list before touching the current queue.
        queue.clear()
        for track in tracks:
            path=video_url(track['video_id'])
            item=xbmcgui.ListItem(label=track['title'],path=path)
            item.setProperty('IsPlayable','true')
            queue.add(path,item)
        if result.get('more'):
            xbmcgui.Dialog().notification('Nebelkind Musik','Die ersten %d Titel werden abgespielt. Weitere Titel: YouTube-Playlist im Kontextmenü.'%len(tracks),time=7000)
        xbmc.Player().play(queue)
    except (OSError,ValueError,KeyError):
        xbmcgui.Dialog().ok('Nebelkind Musik','Die Playlist konnte nicht geladen werden. Sie ist möglicherweise privat, gelöscht oder gerade nicht erreichbar. Bitte versuche eine andere Playlist oder öffne sie über das Kontextmenü im YouTube-Addon.')


def list_tracks(playlist_id):
    import sys,xbmcplugin
    from resources.lib.catalog import load_tracks
    try:result=load_tracks(playlist_id,track_cache())
    except (OSError,ValueError):
        xbmcgui.Dialog().ok('Nebelkind Musik','Diese Playlist ist nicht erreichbar oder enthält keine verfügbaren Titel.')
        xbmcplugin.endOfDirectory(int(sys.argv[1]),succeeded=False)
        return
    for track in result['tracks']:
        path=video_url(track['video_id'])
        item=xbmcgui.ListItem(label=track['title'],path=path)
        item.setProperty('IsPlayable','true')
        xbmcplugin.addDirectoryItem(int(sys.argv[1]),path,item,isFolder=False)
    if result.get('more'):
        item=xbmcgui.ListItem(label='Vollständige Playlist im YouTube-Addon öffnen')
        xbmcplugin.addDirectoryItem(int(sys.argv[1]),playlist_url(playlist_id,browse=True),item,isFolder=True)
    xbmcplugin.endOfDirectory(int(sys.argv[1]))
