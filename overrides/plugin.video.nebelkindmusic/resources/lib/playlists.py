import sys
import xbmcgui
import xbmcplugin

from resources.lib.json_loader import load_data
from resources.lib.image_utils import resolve_image
from resources.lib.utils import build_url
from resources.lib.player import playlist_url


def list_playlists(category):
    handle = int(sys.argv[1])
    data = load_data()

    categories = data.get("categories", {})
    category_data = categories.get(category, {})

    playlists = category_data.get("playlists", [])

    if not isinstance(playlists, list):
        playlists = []

    for pl in playlists:
        url = build_url({
            "action": "play",
            "playlist_id": pl["playlist_id"]
        })

        li = xbmcgui.ListItem(label=pl["name"])
        li.setProperty("IsPlayable", "false")
        li.setInfo('video', {'title': pl['name'], 'plot':
            'Musik-Playlist · %s. OK: Alle abspielen. Kontextmenü: Titelliste öffnen.' % pl.get('owner', 'YouTube')})
        li.addContextMenuItems([
            ('Titelliste öffnen', 'Container.Update(%s)' % build_url({'action':'tracks','playlist_id':pl['playlist_id']})),
            ('Vollständige Playlist im YouTube-Addon öffnen', 'ActivateWindow(Videos,%s,return)' % playlist_url(pl['playlist_id'],browse=True))])


        thumb = (resolve_image(pl.get('image'), 'playlists') or
                 resolve_image(category_data.get('image'), 'categories'))
        if thumb:
            li.setArt({
                "thumb": thumb,
                "icon": thumb,
                "fanart": thumb
            })

        xbmcplugin.addDirectoryItem(
            handle=handle,
            url=url,
            listitem=li,
            isFolder=False
        )

    xbmcplugin.endOfDirectory(handle)
