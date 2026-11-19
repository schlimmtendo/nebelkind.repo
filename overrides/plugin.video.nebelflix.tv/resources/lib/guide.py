"""TV guide overlay with four half-hour windows; touch and remote support.

This is a Python WindowXMLDialog, not the native Kodi PVR timeline. Channel
playback uses the original provider URL.
"""
import time
import xbmc
import xbmcgui
from . import channels, epg, art
from .ui import url as addon_url
from .common import ASSETS, friendly_time, settings, change_settings
from . import load_timing

ACTION_PARENT_DIR=9
ACTION_PREVIOUS_MENU=10
ACTION_NAV_BACK=92
ACTION_CONTEXT_MENU=117
PAGE_SIZE=40


def guide_groups():
    return [('favorites', 'Favoriten'), ('recent', 'Zuletzt gesehen')] + channels.group_catalog()


class Guide(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self._groups = guide_groups()
        group_ids = [slug for slug, _ in self._groups]
        saved = settings().get('guide_group', '')
        self.group = saved if saved in group_ids else (group_ids[2] if len(group_ids) > 2 else 'favorites')
        self.offset = 0
        self._items = []
        self._current = max(0, int(settings().get('guide_row', 0)))
        self.page = max(0, int(settings().get('guide_page', 0)))
        self._conn = None
        self._index = None
        self._group_rows = {}

    def onInit(self):
        started=time.monotonic()
        xbmcgui.Window(10000).setProperty('NFTV.GuideAlive','true')
        xbmcgui.Window(10000).clearProperty('NFTV.ReturnGuide')
        self._conn=epg.connect()
        self._index=epg.guide_index(self._conn)
        self.setProperty('NebelflixTitle','NEBELFLIX TV')
        self._populate()
        load_timing.record('tv','guide.open',started,rows=len(self._items))
        try:
            self._focus_rows_or_group()
        except Exception:
            pass

    def _focus_rows_or_group(self):
        # Kodi cannot focus an empty list. Focus the group selector instead
        # and make it immediately usable with Enter/OK on remotes.
        wanted = 100 if self._items else 11
        try:
            self.setFocusId(wanted)
        except (TypeError, RuntimeError) as exc:
            xbmc.log('[NebelflixTV] Guide focus %d: %s' % (wanted, exc), xbmc.LOGWARNING)

    def _populate(self):
        started=time.monotonic()
        groupname=next((s for g,s in self._groups if g==self.group),self.group)
        self.setProperty('GroupName',groupname)
        now=int(time.time())
        # Align the whole guide to a 30-minute grid.
        now=now - now%1800 + self.offset*1800
        for i in range(4):
            self.setProperty('Time%d'%(i+1),friendly_time(now+i*1800))
        if self.group not in self._group_rows:
            self._group_rows[self.group] = channels.list_group(self.group)
        rows=self._group_rows[self.group]
        self.page=min(self.page, max(0,(len(rows)-1)//PAGE_SIZE))
        first=self.page*PAGE_SIZE
        self._items=rows[first:first+PAGE_SIZE]
        screen=self.getControl(100)
        screen.reset()
        shown=[]
        favorites=channels.favorites()
        for ch in self._items:
            li=xbmcgui.ListItem(label=('★ ' if ch['id'] in favorites else '')+ch['name'])
            state=epg.programme(ch,conn=self._conn,index=self._index)
            img=art.logo_for(ch,state)
            li.setArt({'thumb':img or str(ASSETS/'resources/media/channel-placeholder.png'),'icon':img or str(ASSETS/'resources/media/channel-placeholder.png')})
            li.setProperty('Source',ch['source'])
            # Expose a true programme info line for the selected station.
            # These times come from the same XMLTV entries as the OSD.
            current = state.get('now')
            upcoming = state.get('next')
            li.setProperty('NF.Now',
                ('%s–%s · %s' % (friendly_time(current[0]),
                                  friendly_time(current[1]), current[2]))
                if current else 'Keine aktuelle Sendung im EPG')
            li.setProperty('NF.Next',
                ('%s–%s · %s' % (friendly_time(upcoming[0]),
                                  friendly_time(upcoming[1]), upcoming[2]))
                if upcoming else 'Keine Folgesendung im EPG')
            for i in range(4):
                stamp=now+i*1800+1
                program=epg.at_time(ch,stamp,self._conn,self._index)
                li.setProperty('Show%d'%(i+1),str(program[2])[:38] if program else '—')
            shown.append(li)
        screen.addItems(shown)
        if shown:
            self._current = max(0, min(self._current, len(shown) - 1))
            screen.selectItem(self._current)
        else:
            self._current = 0
        self.setProperty('GuideEmpty', 'true' if not shown else '')
        self.setProperty('GuideEmptyText',
                         'Noch keine angesehenen TV-Sender. Starte einen Sender, dann erscheint er hier.'
                         if self.group == 'recent' else
                         'Noch keine TV-Favoriten. Sender über das Kontextmenü hinzufügen.'
                         if self.group == 'favorites' else
                         'Diese Gruppe enthält keine verfügbaren Sender. Bitte andere Gruppe wählen.'
                         if not shown else '')
        self.setProperty('Count','%d–%d von %d Sendern' % (first+1 if rows else 0, min(first+PAGE_SIZE,len(rows)), len(rows)))
        info = ('EPG wird vorbereitet: In Nebelflix TV unter Verwalten die EPG-Quellen aktualisieren.'
                if not epg.source_status() else
                'Vavoo TV Plus · XMLTV aus mehreren Quellen · Keine EPG-Daten werden erfunden')
        self.setProperty('Info',info)
        load_timing.record('tv','guide.populate',started,rows=len(self._items))

    def onClick(self,controlId):
        if controlId==100:
            index=self.getControl(100).getSelectedPosition()
            if 0<=index<len(self._items):
                cid=self._items[index]['id']
                change_settings(guide_row=index, guide_page=self.page)
                home = xbmcgui.Window(10000)
                home.setProperty('NFTV.ReturnGuide', '1')
                home.setProperty('NF.PlaybackOwner', 'tv')
                home.clearProperty('NF.PendingPlay')
                home.clearProperty('NF.ResumeFallback')
                home.clearProperty('NF.Item')
                home.clearProperty('NFD.Item')

                xbmcgui.Window(10000).clearProperty('NFTV.StopGuard')
                xbmcgui.Window(10000).clearProperty('NFTV.UserStop')
                # Keep the modal and its SQLite/index/cache alive behind the
                # fullscreen player. Stop/Back reveals this exact guide again.
                xbmc.executebuiltin('RunPlugin(%s)' % addon_url('play',id=cid))
        elif controlId==11:
            self._groups = guide_groups()
            i=xbmcgui.Dialog().select('Sendergruppe auswählen', [name for _,name in self._groups],
                preselect=next((j for j,(g,_) in enumerate(self._groups) if g==self.group),0))
            if i>=0:
                self._group_rows.clear()
                self.group=self._groups[i][0]
                change_settings(guide_group=self.group)
                self._current=0
                self.page=0
                self._populate()
                self._focus_rows_or_group()
        elif controlId==12:
            self.offset=max(-2,self.offset-2)
            self._current=self.getControl(100).getSelectedPosition()
            self._populate()
        elif controlId==13:
            self.offset=min(96,self.offset+2)
            self._current=self.getControl(100).getSelectedPosition()
            self._populate()
        elif controlId==14:
            self.offset=0
            self._populate()
        elif controlId==15:
            self.group='favorites'
            change_settings(guide_group='favorites')
            self._group_rows.pop('favorites',None)
            self.page=0;self._current=0
            self._populate()
            self._focus_rows_or_group()
        elif controlId==16:
            self.close()
        elif controlId==17:
            self.page=max(0,self.page-1)
            self._current=0
            self._populate()
        elif controlId==18:
            self.page+=1
            self._current=0
            self._populate()

    def onAction(self,action):
        if action.getId() in (ACTION_PARENT_DIR,ACTION_PREVIOUS_MENU,ACTION_NAV_BACK):
            # Back returns from playback or selects the explicit exit button.
            # Only Zurück at the top closes the guide itself.
            if xbmc.Player().isPlayingVideo():
                xbmc.Player().stop()
                self._focus_rows_or_group()
            else:
                self.setFocusId(16)
        elif action.getId()==ACTION_CONTEXT_MENU:
            self.context_menu()

    def context_menu(self):
        pos=self.getControl(100).getSelectedPosition()
        if not 0<=pos<len(self._items):
            return
        ch=self._items[pos]
        active=ch['id'] in channels.favorites()
        choice=xbmcgui.Dialog().contextmenu([
            'TV-Favorit entfernen' if active else 'TV-Favorit hinzufügen',
            'EPG für diesen Sender'])
        if choice==0:
            channels.set_favorite(ch['id'],not active)
            self._group_rows.pop('favorites',None)
            self._current=pos
            self._populate()
            self._focus_rows_or_group()
            xbmcgui.Dialog().notification('Nebelflix TV',
                'Favorit entfernt' if active else 'Zu Favoriten hinzugefügt',xbmcgui.NOTIFICATION_INFO,2000)
        elif choice==1:
            from . import epg
            _,events=epg.schedule(ch)
            xbmcgui.Dialog().textviewer(ch['name'], '\n'.join(
                '%s–%s  %s' % (friendly_time(row[0]),friendly_time(row[1]),row[2]) for row in events)
                or 'Keine Programmdaten verfügbar')

    def close(self):
        home=xbmcgui.Window(10000)
        home.clearProperty('NFTV.GuideAlive')
        home.clearProperty('NFTV.ReturnGuide')
        home.clearProperty('NFTV.RestoreGuide')
        if self._conn is not None:
            try: self._conn.close()
            except Exception: pass
            self._conn=None
        super(Guide,self).close()


def open_guide():
    view=Guide('Guide.xml',str(ASSETS),'Default','1080i')
    try:
        view.doModal()
    finally:
        try:view.close()
        except Exception:pass
        del view
