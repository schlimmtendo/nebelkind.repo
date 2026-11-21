"""Separate persistent script; release startup latch even if imports fail."""
import xbmcgui

if __name__=='__main__':
    home=xbmcgui.Window(10000)
    try:
        import controller
        controller.monitor()
    finally:
        home.clearProperty('NF.ControllerStarting')
        home.clearProperty('NF.Heartbeat')
