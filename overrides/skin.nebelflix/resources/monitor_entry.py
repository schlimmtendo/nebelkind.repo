"""Separate persistent script; release startup latch even if imports fail."""
import xbmcgui
import xbmc
import time

if __name__=='__main__':
    home=xbmcgui.Window(10000)
    try:
        started=time.monotonic()
        import controller
        xbmc.log('[Nebelflix Startup] Controller import: %.0f ms' % ((time.monotonic()-started)*1000),xbmc.LOGINFO)
        controller.monitor()
    finally:
        home.clearProperty('NF.SetupLaunchRequested')
        home.clearProperty('NF.ControllerStarting')
        home.clearProperty('NF.Heartbeat')
