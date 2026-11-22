"""Refresh music discovery after startup without blocking Kodi's interface."""
import xbmc
import xbmcaddon
from resources.lib.catalog import refresh
from resources.lib.json_loader import paths


def run():
    monitor = xbmc.Monitor()
    if monitor.waitForAbort(45):
        return
    addon = xbmcaddon.Addon('plugin.video.nebelkindmusic')
    while not monitor.abortRequested():
        if addon.getSetting('catalog.autoupdate') != 'false':
            try:
                count = refresh(*paths(), abort=monitor.abortRequested)
                if count:
                    xbmc.log('[Nebelkind Musik] Playlist-Katalog: %d Kategorien aktualisiert' % count, xbmc.LOGINFO)
            except Exception as error:
                xbmc.log('[Nebelkind Musik] Katalog behalten: ' + type(error).__name__, xbmc.LOGWARNING)
        if monitor.waitForAbort(3600):
            return


if __name__ == '__main__':
    run()
