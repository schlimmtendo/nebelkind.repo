"""Nebelflix TV: Kodi cross-platform Python source and guide frontend."""
import sys
from urllib.parse import parse_qsl
import xbmcgui
import xbmcplugin

# Nebelkind installed-skin gate (not an activation check)
from resources.lib.nebelkind_skin_gate import require_skin as _nebelkind_require_skin
if not _nebelkind_require_skin(sys.argv):
    raise SystemExit(0)

from resources.lib import ui,channels
from resources.lib.common import change_settings,notify,log


def run(handle,params):
    action=params.get('action','root')
    if action in ('root','guide','groups','list','schedule','search'):
        # First explicit TV usage activates gentle background maintenance.
        change_settings(sync_started=True)
    if action=='root':
        ui.root(handle)
    elif action=='guide':
        ui.guide()
        xbmcplugin.endOfDirectory(handle,succeeded=True)
    elif action=='groups': ui.groups(handle)
    elif action=='list':
        group=params.get('group','all')
        if group in ('recent','favorites') or group in {slug for slug,_ in channels.group_catalog()}:
            # Remember category cards and regular add-on group browsing too.
            change_settings(guide_group=group)
        ui.list_channels(handle,group,search=params.get('search',''),page=int(params.get('page','0')))
    elif action=='schedule': ui.schedule(handle,params.get('id',''))
    elif action=='search': ui.search(handle)
    elif action=='favorite':
        ident=params.get('id','')
        if channels.find_channel(ident):
            active=ident in channels.favorites()
            channels.set_favorite(ident,not active)
            notify('Favorit entfernt' if active else 'Zu Favoriten hinzugefügt')
        xbmcplugin.endOfDirectory(handle,succeeded=True)
        import xbmc
        xbmc.executebuiltin('Container.Refresh')
    elif action=='play':
        from resources.lib.playback import play_station
        xbmcplugin.endOfDirectory(handle,succeeded=True)
        play_station(params.get('id',''))
    elif action=='manage':
        from resources.lib.manage import manage
        manage()
        xbmcplugin.endOfDirectory(handle,succeeded=True)
    else:
        xbmcplugin.endOfDirectory(handle,succeeded=False)


if __name__=='__main__':
    handle=int(sys.argv[1]) if len(sys.argv)>1 else -1
    params=dict(parse_qsl(sys.argv[2].lstrip('?'),keep_blank_values=True)) if len(sys.argv)>2 else {}
    try:run(handle,params)
    except Exception as exc:
        log('GUI: %s' % exc)
        xbmcgui.Dialog().ok('Nebelflix TV','Ein Fehler ist aufgetreten:\n%s' % exc)
        try:xbmcplugin.endOfDirectory(handle,succeeded=False)
        except Exception:pass
