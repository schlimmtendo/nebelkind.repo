import sys
import xbmcplugin
import xbmcgui
from urllib.parse import parse_qsl

from resources.lib.categories import list_categories
from resources.lib.playlists import list_playlists


def router(paramstring):
    handle = int(sys.argv[1])
    params = dict(parse_qsl(paramstring[1:]))

    action = params.get("action")

    if action == "category":
        list_playlists(params.get("category"))

    elif action == "tracks":
        from resources.lib.player import list_tracks
        list_tracks(params.get("playlist_id"))

    elif action == "play":
        from resources.lib.player import play_playlist
        play_playlist(params.get('playlist_id'))

    else:
        list_categories()
