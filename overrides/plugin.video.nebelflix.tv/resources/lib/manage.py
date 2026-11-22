"""Widget management; Vavoo owns favourites and its EPG interface."""
import xbmcgui
from . import channels
from .vavoo_bridge import open_guide,open_favorites


def manage():
    dialog=xbmcgui.Dialog()
    choice=dialog.select('Nebelflix TV · Widgets & Vavoo',[
        'Vavoo-Programmführer öffnen','Vavoo-Favoriten verwalten','Widget-Sender aus Vavoo neu einlesen'])
    if choice==0:open_guide()
    elif choice==1:open_favorites()
    elif choice==2:
        with _progress():
            rows=channels.channel_list(force=True)
        dialog.notification('Nebelflix TV','%d Sender für Widgets eingelesen.'%len(rows))


class _progress:
    def __enter__(self):
        self.dialog=xbmcgui.DialogProgressBG()
        self.dialog.create('Nebelflix TV','Widget-Sender werden eingelesen …')
        return self
    def __exit__(self,*args):self.dialog.close()
