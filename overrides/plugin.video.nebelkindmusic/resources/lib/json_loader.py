from pathlib import Path
import xbmcaddon
import xbmcvfs
from resources.lib.catalog import load_catalog


def paths():
    addon = xbmcaddon.Addon('plugin.video.nebelkindmusic')
    seed = Path(xbmcvfs.translatePath(addon.getAddonInfo('path'))) / 'resources/data/playlists.json'
    cache = Path(xbmcvfs.translatePath(addon.getAddonInfo('profile'))) / 'playlist-catalog.json'
    return seed, cache


def load_data():
    return load_catalog(*paths())
