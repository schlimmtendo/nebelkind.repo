"""Stable plugin routes; never import another addon's UI/runtime namespace."""
from urllib.parse import urlencode,urlsplit,parse_qs
import xbmc
import xbmcgui

VAVOO='plugin.video.vavoo.tv.plus'


def launch(action,channel=None):
    if not xbmc.getCondVisibility('System.HasAddon('+VAVOO+')'):
        xbmcgui.Dialog().ok('Nebelflix TV','Vavoo TV Plus muss installiert und aktiviert sein.')
        return False
    params={'action':action}
    if channel:
        raw=channel.get('url','')
        if urlsplit(raw).netloc==VAVOO:
            raw=parse_qs(urlsplit(raw).query).get('url',[''])[0]
        if raw:params['url']=raw
    xbmc.executebuiltin('RunPlugin(plugin://'+VAVOO+'/?'+urlencode(params)+')')
    return True


def open_guide(channel=None):return launch('favorites_epg',channel)
def open_favorites():return launch('favorites_manager')
