"""Short-lived action entry point, separate from the persistent controller."""
import sys
import json
import time
import xbmc
import xbmcgui
import load_timing
import hub_protocol


def quick_tab(args):
    if not args or not args[0].isdigit():
        return
    home = xbmcgui.Window(10000)
    if home.getProperty('NF.Revisions.Running') == 'true':
        return
    page = home.getProperty('NF.TabPage' + args[0])
    if page:
        slot = int(args[0])
        if not 0 <= slot < 12:
            return
        home.clearProperty('NF.HubSwitching')
        home.setProperty('NF.HubRequestedAt', str(time.monotonic()))
        home.setProperty('NF.StopSpotlight', 'true')
        home.setProperty('NF.Page', page)
        segment = int(home.getProperty('NF.HubSegment'+str(slot)) or 0)
        window = hub_protocol.window_id(slot,segment)
        xbmc.executebuiltin('ActivateWindow(%s)' % ('Home' if window==10000 else str(window)))


def quick_page(args):
    if not args or args[0] not in ('1','-1'):
        return
    home=xbmcgui.Window(10000)
    if home.getProperty('NF.Revisions.Running') == 'true':
        return
    current=xbmcgui.Window(xbmcgui.getCurrentWindowId())
    raw=current.getProperty('NF.HubSlot')
    if not raw.isdigit():
        return
    slot=int(raw); segment=int(current.getProperty('NF.Segment') or 0)+int(args[0])
    if not 0 <= slot < 12 or not 0 <= segment < 6:
        return
    home.setProperty('NF.HubRequestedAt',str(time.monotonic()))
    window=hub_protocol.window_id(slot,segment)
    xbmc.executebuiltin('ActivateWindow(%s)' % ('Home' if window==10000 else str(window)))


if __name__ == '__main__':
    started = time.monotonic()
    command = sys.argv[1] if len(sys.argv) > 1 else 'diagnostic'
    queued=False
    try:
        if command == 'tab':
            quick_tab(sys.argv[2:])
        elif command == 'hub_page':
            quick_page(sys.argv[2:])
        elif command=='skip_intro':
            xbmcgui.Window(10000).setProperty('NF.SkipIntro','true')
        elif command in ('details','trailer','play','mylist') and (
                xbmcgui.Window(10000).getProperty('NF.Heartbeat') or
                xbmcgui.Window(10000).getProperty('NF.ControllerStarting')):
            queued=True
            # Reuse the persistent controller. Re-importing it for each remote
            # click took tens of seconds on the affected installation.
            xbmcgui.Window(10000).setProperty('NF.PendingAction',json.dumps(
                dict(command=command,args=sys.argv[2:],at=time.time())))
        else:
            import controller
            load_timing.record('skin', 'action.import', started)
            controller.dispatch(command, sys.argv[2:])
    except Exception as exc:
        xbmc.log('[Nebelflix] %s: %s' % (type(exc).__name__, exc), xbmc.LOGERROR)
        xbmcgui.Dialog().notification('Nebelflix', 'Aktion fehlgeschlagen. Details stehen im Kodi-Protokoll.')
        raise
    finally:
        load_timing.record('skin', ('action.queue.' if queued else 'action.') + command, started)
