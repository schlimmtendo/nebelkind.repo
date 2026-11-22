import sys
import xbmcplugin
from urllib.parse import parse_qsl

from resources.lib.categories import list_categories
from resources.lib.playlists import list_playlists


def router(paramstring):
    handle = int(sys.argv[1])
    params = dict(parse_qsl(paramstring.lstrip('?')))
    action = params.get('action')

    if action == 'category':
        list_playlists(params.get('category'))
    elif action in ('tracks', 'play'):
        from resources.lib.player import list_tracks, play_playlist

        details = {
            'name': params.get('name', ''),
            'playlist_image': params.get('playlist_image', ''),
            'category_image': params.get('category_image', ''),
        }
        if action == 'tracks':
            list_tracks(params.get('playlist_id'), **details)
        else:
            play_playlist(params.get('playlist_id'), **details)
    else:
        list_categories()
