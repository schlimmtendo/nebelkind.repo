"""Playlist navigation and playback via the installed Kodi YouTube add-on."""

import random
import re
from urllib.parse import urlencode

import xbmc
import xbmcgui

from resources.lib.catalog import PLAYLIST_ID
from resources.lib.image_utils import resolve_image


PLAYBACK_MODES = ('Normal', 'Zufall', 'Rückwärts')


def playlist_url(playlist_id, browse=False):
    if not PLAYLIST_ID.fullmatch(str(playlist_id or '')):
        raise ValueError('Keine normale öffentliche YouTube-Playlist')
    if browse:
        return 'plugin://plugin.video.youtube/playlist/' + playlist_id + '/'
    return 'plugin://plugin.video.youtube/play/?' + urlencode(
        {'playlist_id': playlist_id, 'action': 'play'})


def track_cache():
    import xbmcaddon
    import xbmcvfs
    from pathlib import Path

    addon = xbmcaddon.Addon('plugin.video.nebelkindmusic')
    return Path(xbmcvfs.translatePath(addon.getAddonInfo('profile'))) / 'playlist-tracks'


def video_url(video_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', str(video_id)):
        raise ValueError('Ungültiger Titel')
    return 'plugin://plugin.video.youtube/play/?' + urlencode({'video_id': video_id})


def get_playlist_details(playlist_id, name='', playlist_image='', category_image=''):
    """Return the same artwork as on the selected playlist tile.

    For old bookmarks/shortcuts without the new parameters, look up the item in
    the local catalogue. Never inherit art from the previous Kodi playback.
    """
    if not (name and (playlist_image or category_image)):
        from resources.lib.json_loader import load_data

        for category in load_data().get('categories', {}).values():
            for playlist in category.get('playlists', []):
                if playlist.get('playlist_id') == playlist_id:
                    name = name or playlist.get('name', '')
                    playlist_image = playlist_image or playlist.get('image', '')
                    category_image = category_image or category.get('image', '')
                    break
            if name and (playlist_image or category_image):
                break

    thumb = (resolve_image(playlist_image, 'playlists') or
             resolve_image(category_image, 'categories'))
    return name or 'Nebelkind Musik', thumb


def make_video_item(track, title, artwork):
    """Supply Kodi/skins with fresh per-track video metadata and artwork."""
    item = xbmcgui.ListItem(label=track['title'], path=video_url(track['video_id']))
    item.setProperty('IsPlayable', 'true')
    item.setInfo('video', {
        'title': track['title'],
        'plot': 'Nebelkind Musik · Playlist: ' + title,
    })
    item.setProperty('NebelkindMusic.PlaylistTitle', title)
    if artwork:
        # VideoPlayer.Art(fanart)/ListItem.Art(fanart) must never be inherited
        # from a previously played movie or another playlist item.
        item.setArt({
            'thumb': artwork,
            'icon': artwork,
            'poster': artwork,
            'fanart': artwork,
            'landscape': artwork,
        })
        item.setProperty('NebelkindMusic.PlaylistThumb', artwork)
    return item


def reorder_tracks(tracks, mode):
    """Reorder a copy; never modify the cached original track sequence."""
    ordered = list(tracks)
    if mode == 1:
        random.shuffle(ordered)
    elif mode == 2:
        ordered.reverse()
    return ordered


def play_playlist(playlist_id, name='', playlist_image='', category_image=''):
    from resources.lib.catalog import load_tracks

    # Select BEFORE touching the existing queue. Cancelling leaves playback alone.
    mode = xbmcgui.Dialog().select('Playlist abspielen – Reihenfolge', list(PLAYBACK_MODES))
    if mode not in (0, 1, 2):
        return

    try:
        title, artwork = get_playlist_details(playlist_id, name, playlist_image, category_image)
        result = load_tracks(playlist_id, track_cache())
        tracks = reorder_tracks(result['tracks'], mode)
        if not tracks:
            raise ValueError('Keine Titel')

        # Resolve and construct the new queue before clearing the previous one.
        prepared = [(video_url(track['video_id']), make_video_item(track, title, artwork))
                    for track in tracks]
        queue = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        queue.clear()
        for path, item in prepared:
            queue.add(path, item)

        if result.get('more'):
            xbmcgui.Dialog().notification(
                'Nebelkind Musik',
                'Die ersten %d Titel werden abgespielt. Weitere Titel: YouTube-Playlist im Kontextmenü.' % len(tracks),
                time=7000,
            )
        xbmc.Player().play(queue)
    except (OSError, ValueError, KeyError):
        xbmcgui.Dialog().ok(
            'Nebelkind Musik',
            'Die Playlist konnte nicht geladen werden. Sie ist möglicherweise privat, gelöscht oder gerade nicht erreichbar. Bitte versuche eine andere Playlist oder öffne sie über das Kontextmenü im YouTube-Addon.',
        )


def list_tracks(playlist_id, name='', playlist_image='', category_image=''):
    import sys
    import xbmcplugin
    from resources.lib.catalog import load_tracks

    try:
        result = load_tracks(playlist_id, track_cache())
    except (OSError, ValueError):
        xbmcgui.Dialog().ok('Nebelkind Musik', 'Diese Playlist ist nicht erreichbar oder enthält keine verfügbaren Titel.')
        xbmcplugin.endOfDirectory(int(sys.argv[1]), succeeded=False)
        return

    title, artwork = get_playlist_details(playlist_id, name, playlist_image, category_image)
    for track in result['tracks']:
        path = video_url(track['video_id'])
        item = make_video_item(track, title, artwork)
        xbmcplugin.addDirectoryItem(int(sys.argv[1]), path, item, isFolder=False)
    if result.get('more'):
        item = xbmcgui.ListItem(label='Vollständige Playlist im YouTube-Addon öffnen')
        xbmcplugin.addDirectoryItem(int(sys.argv[1]), playlist_url(playlist_id, browse=True), item, isFolder=True)
    xbmcplugin.endOfDirectory(int(sys.argv[1]))
