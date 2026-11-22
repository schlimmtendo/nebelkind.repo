"""Independent native Kodi hub windows and a small, non-blocking scheduler."""
import json
import time

import xbmc
import xbmcgui
import library
import direct_widgets
import load_timing
import performance
import revisions_updater
import hub_protocol
import logo_art
import setup_wizard
from manual_trailer import ManualTrailer
from bounded_workers import SearchExecutor


ROWS_PER_VIEW = hub_protocol.ROWS_PER_VIEW


def window_id(slot, segment=0):
    return hub_protocol.window_id(slot,segment)


def setup(force=False):
    """One-time configuration; an update never replaces an existing layout."""
    state = library.read('hub-setup.json', {})
    if state.get('completed') and not force:
        return False
    dialog = xbmcgui.Dialog()
    existing = (library.ROOT / 'layout.json').exists()
    home=xbmcgui.Window(10000)
    previous_block=home.getProperty('NF.StartupBlocked')
    home.setProperty('NF.StartupBlocked','true')
    try:profile=setup_wizard.run(home,xbmc.Monitor(),existing=existing)
    except Exception:
        home.setProperty('NF.StartupBlocked',previous_block)
        raise
    if profile is None:
        home.setProperty('NF.StartupBlocked',previous_block)
        return False
    if profile in (0, 1):
        settings = library.read('performance.json', {})
        settings['profile'] = 'auto' if profile == 0 else 'low'
        library.write('performance.json', settings)
    if xbmc.getCondVisibility('System.HasAddon(script.buildguard)'):
        import xbmcaddon
        guard=xbmcaddon.Addon('script.buildguard')
        if guard.getSetting('startup.cache')=='true' and dialog.yesno(
                'Gespeicherte Inhalte beim Start behalten',
                'BuildGuard leert derzeit nach jedem Start den Kodi-Cache. '
                'Diese Startbereinigung ausschalten, damit gespeicherte Widgets schneller wieder erscheinen?'):
            guard.setSetting('startup.cache','false')
    # The bundled layout is a fallback. Do not write it over a personal layout.
    library.write('hub-setup.json', dict(completed=True, version=1, at=time.time()))
    home.setProperty('NF.FullPreparationRequested','true')
    dialog.notification('Nebelflix', 'Einrichtung übernommen · Alle Bereiche werden vorbereitet', time=3500)
    return True


class Hubs:
    def __init__(self, api):
        self.api = api
        self.home = api.WIN
        self.budget = performance.Budget(library.read('performance.json', {}).get('profile', 'auto'))
        self.config = library.layout()
        self.layout_stamp = None
        self.refresh_stamp = api.prop('RefreshWidgets')
        self.active = None
        self.active_window = None
        self.windows = {}
        self.jobs = set()
        self.tasks = {}
        self.explicit_requests = {}
        self.inflight = set()
        self.logo_pool = SearchExecutor(max_workers=1)
        self.logo_jobs = {}
        self.logo_attempted = set()
        self.next_logo_check = 0
        self.pool = SearchExecutor(max_workers=self.budget.limits['requests'])
        self.refreshed = set()
        self.visited = set()
        self.next_freshness_check = 0
        self.tv_revision=api.prop('TVRevision')
        self.load_config()

    def load_config(self):
        self.config = library.layout()
        for slot in range(library.MAX_TABS):
            tab = self.config['tabs'][slot] if slot < len(self.config['tabs']) else {}
            self.api.prop('TabLabel'+str(slot), tab.get('label', ''))
            self.api.prop('TabPage'+str(slot), tab.get('id', ''))
            if not self.api.prop('TabWindow'+str(slot)):
                self.api.prop('TabWindow'+str(slot),'Home' if slot==0 else str(window_id(slot)))
        self.layout_stamp = self.api.prop('LayoutChanged')

    def bind(self, window, page, cid, row, source=None, offset=0, back=False, refresh=False):
        path = source or row['path']
        if not refresh and offset==0 and not back:
            entry=direct_widgets.cached(path)
            if entry is not None and direct_widgets.restore(window,self.home,page,cid,path,entry,row['label'].lower().startswith('top 10'),budget=self.budget):
                return
        window.clearProperty('NF.RowNative'+str(cid))
        if not refresh:self.refreshed.discard((page,cid))
        hub_protocol.set_property(window,'NF.RowNetwork'+str(cid),'true' if refresh or direct_widgets.cached(path) is None else '')
        hub_protocol.set_property(window,'NF.RowLoading'+str(cid), 'true')
        hub_protocol.set_property(self.home,direct_widgets.key(page, cid)+'.State', 'queued')
        request = str(time.time_ns()) if refresh else hub_protocol.request_key(page,cid,path,offset)
        hub_protocol.set_property(self.home,direct_widgets.key(page,cid)+'.Request', request)
        hub_protocol.set_property(window,'NF.RowURL'+str(cid), direct_widgets.url(
            page, cid, path, row['label'].lower().startswith('top 10'),
            request if refresh else '', offset=offset, back=back, request=request))
        hub_protocol.set_property(window,'NF.Source'+str(cid), json.dumps(dict(source=path, offset=offset)))
        self.jobs.add((page, cid))
        hub_protocol.set_property(self.home,direct_widgets.key(page,cid)+'.Job',request)
        hub_protocol.set_property(self.home,direct_widgets.key(page,cid)+'.Started',str(time.time()))
        future=self.pool.submit(direct_widgets.contents,path,refresh)
        self.inflight.add(future)
        self.tasks[(page,cid)]=(future,window,path,offset,back,request,row)

    def apply_spotlight_setting(self):
        if self.api.prop('SpotlightChanged')!='true':return
        self.api.prop('SpotlightChanged','')
        if not xbmc.getCondVisibility('Skin.HasSetting(NFHideSpotlight)'):return
        window=xbmcgui.Window(xbmcgui.getCurrentWindowId())
        slot=window.getProperty('NF.HubSlot')
        if not slot.isdigit() or window.getFocusId() not in (100,101,102):return
        if not 0<=int(slot)<len(self.config['tabs']):return
        rows=self.config['tabs'][int(slot)].get('rows',[])[:48]
        for index,row in enumerate(rows):
            cid=600+index
            if self.enabled(row) and int(window.getProperty('NF.TileCount'+str(cid)) or 0)>0:
                window.setFocusId(cid);return
        window.setFocusId(hub_protocol.topbar_id(int(slot)))

    def restore_detail_focus(self):
        if self.api.prop('RestoreWidgetFocus')!='true':return
        self.api.prop('RestoreWidgetFocus','')
        try:target=json.loads(self.api.prop('DetailOrigin') or '{}')
        except ValueError:return
        active=xbmcgui.getCurrentWindowId()
        if target.get('window')!=active:return
        window=xbmcgui.Window(active)
        cid=target.get('row',0)
        if not 600<=cid<648 or window.getProperty('NF.Page')!=target.get('page'):return
        count=int(window.getProperty('NF.TileCount'+str(cid)) or 0)
        if not count:return
        try:
            window.setFocusId(cid)
            window.getControl(cid).selectItem(min(max(0,int(target.get('position',0))),count-1))
        except RuntimeError:pass

    def presentation_locked(self):
        if xbmc.getCondVisibility('System.HasModalDialog') or xbmc.getCondVisibility('Player.HasVideo'):
            return True
        try:
            window=xbmcgui.Window(xbmcgui.getCurrentWindowId());focus=window.getFocusId()
        except RuntimeError:return True
        return 600<=focus<648 and int(window.getProperty('NF.TileCount'+str(focus)) or 0)>0

    def complete_tasks(self):
        # Downloads can finish into disk cache while the user is browsing, but
        # changing static provider items can reset every list in the window.
        locked=self.presentation_locked()
        modal=xbmc.getCondVisibility('System.HasModalDialog') or xbmc.getCondVisibility('Player.HasVideo')
        self.inflight={job for job in self.inflight if not job.done()}
        applied=0
        for identity,task in list(self.tasks.items()):
            future,window,source,offset,back,request,row=task
            explicit=self.explicit_requests.get(identity)==request
            if locked and (not explicit or modal):continue
            if not future.done():continue
            if applied >= (1 if self.budget.name=='low' else 2):break
            del self.tasks[identity]
            self.explicit_requests.pop(identity,None)
            applied+=1
            page,cid=identity;prefix=direct_widgets.key(page,cid)
            if self.home.getProperty(prefix+'.Request')!=request:continue
            try:
                entry,stale,status=future.result()
                if explicit:window.clearProperty('NF.Source'+str(cid))
                direct_widgets.restore(window,self.home,page,cid,source,entry,
                    row['label'].lower().startswith('top 10'),allow_personal=True,offset=offset,back=back,budget=self.budget)
                hub_protocol.set_property(self.home,prefix+'.Stale','true' if stale else '')
            except Exception as exc:
                retry=dict(Title='Quelle nicht erreichbar · Erneut versuchen',Path=source,
                           DirectNavigate=source,Retry=True,Row=cid,Hub=page)
                direct_widgets.restore(window,self.home,page,cid,source,dict(items=[retry],time=time.time()),allow_personal=True)
                hub_protocol.set_property(self.home,prefix+'.State','error')
                xbmc.log('[Nebelflix Widgets] row=%s %s'%(cid,type(exc).__name__),xbmc.LOGWARNING)
            if explicit and window.getFocusId()==cid:
                try:window.getControl(cid).selectItem(0)
                except RuntimeError:pass
            if window.getFocusId()==cid+1000:
                slot=int(window.getProperty('NF.HubSlot') or 0)
                window.setFocusId(cid if self.home.getProperty(prefix+'.State')=='ready' else hub_protocol.topbar_id(slot))
            self.home.clearProperty(prefix+'.Job')
            hub_protocol.set_property(window,'NF.RowLoading'+str(cid),'')
            self.jobs.discard(identity)

    def tick_logos(self,window,focus):
        if self.presentation_locked():return
        if time.monotonic()<self.next_logo_check:return
        self.next_logo_check=time.monotonic()+1
        # XML controls returned by getControl() do not expose another Python
        # interpreter's ListItem array. Use the authoritative disk snapshot.
        row=focus if 600<=focus<648 else 600
        for cid in (row,row+1):
            if cid>=648 or window.getProperty('NF.RowLoading'+str(cid))=='true':continue
            try:source=json.loads(window.getProperty('NF.Source'+str(cid)) or '{}')
            except ValueError:continue
            path=source.get('source','')
            if path.startswith(('nf://continue','nf://mylist')):continue
            entry=direct_widgets.cached(path) if path else None
            if not entry:continue
            offset=source.get('offset',0)
            try:selected=max(0,window.getControl(cid).getSelectedPosition())
            except RuntimeError:selected=0
            visible_start=offset+max(0,selected-2)
            stamp=[]
            for item in entry.get('items',[])[visible_start:visible_start+8]:
                if item.get('Clearlogo'):continue
                key=logo_art.identity(item)
                if not key:continue
                saved=logo_art.cached(item)
                if saved is not None:
                    if saved.get('logo'):stamp.append((key,saved['logo']))
                elif key not in self.logo_attempted and len(self.logo_jobs)<1 and not self.budget.pressure:
                    self.logo_attempted.add(key)
                    self.logo_jobs[key]=self.logo_pool.submit(logo_art.resolve,dict(item))
            revision=json.dumps(stamp)
            if stamp and window.getProperty('NF.RowLogoStamp'+str(cid))!=revision:
                control=window.getControl(cid)
                page=window.getProperty('NF.Page')
                top10=window.getProperty('NF.RowKind'+str(cid))=='top10'
                direct_widgets.restore(window,self.home,page,cid,path,entry,top10,
                    allow_personal=True,offset=offset,back=bool(window.getProperty('NF.History'+str(cid)) not in ('','[]')),budget=self.budget)
                limit=10 if top10 else (12 if self.budget.name=='low' else 20)
                count=min(limit,max(0,len(entry.get('items',[]))-offset))
                if count:control.selectItem(min(selected,count-1))
                hub_protocol.set_property(window,'NF.RowLogoStamp'+str(cid),revision)
        for key,job in list(self.logo_jobs.items()):
            if job.done():
                try:job.result()
                except Exception as exc:xbmc.log('[Nebelflix Logos] '+type(exc).__name__,xbmc.LOGWARNING)
                del self.logo_jobs[key]

    @staticmethod
    def enabled(row):
        return not ((row['path'].startswith('nf://continue') and
                     xbmc.getCondVisibility('Skin.HasSetting(NFHideContinue)')) or
                    (row['label'].lower().startswith('top 10') and
                     xbmc.getCondVisibility('Skin.HasSetting(NFHideTop10)')))

    def enter(self, window, slot):
        if not 0 <= slot < len(self.config['tabs']):
            return None
        tab = self.config['tabs'][slot]
        page = tab['id']
        segment = 0
        start = segment*ROWS_PER_VIEW
        self.windows[page] = window
        signature = hub_protocol.signature(tab,segment)
        changed = window.getProperty('NF.LayoutSignature') != signature
        if changed:
            hub_protocol.set_property(window,'NF.RowsReady','')
            hub_protocol.set_property(window,'NF.LayoutSignature', signature)
            for cid in range(600, 648):
                for prefix in ('RowURL','RowHeading','RowState','RowKind','RowLoading','RowNative','TileCount','History','Source'):
                    window.clearProperty('NF.'+prefix+str(cid))
            hub_protocol.set_property(window,'NF.Bound', str(start))
        hub_protocol.set_property(window,'NF.Page', page)
        self.api.prop('Page', page)
        self.api.prop('TabSelected', str(slot))
        self.api.prop('HubSegment'+str(slot),str(segment))
        self.api.prop('TabWindow'+str(slot),'Home' if window_id(slot,segment)==10000 else str(window_id(slot,segment)))
        hub_protocol.set_property(window,'NF.NextWidgetsWindow',str(window_id(slot,min(5,segment+1))))
        previous=window_id(slot,max(0,segment-1))
        hub_protocol.set_property(window,'NF.PreviousWidgetsWindow','Home' if previous==10000 else str(previous))
        hub_protocol.set_property(window,'NF.HasPreviousWidgets','true' if segment else '')
        hub_protocol.set_property(window,'NF.HasMoreWidgets','true' if start+ROWS_PER_VIEW < len(tab.get('rows',[])) else '')
        self.api.prop('DirectHubs', 'true')
        self.api.prop('HubSwitching', '')
        self.api.prop('HasHero', 'true' if page in ('home','movie','tv','new') else 'false')
        if self.active_window != (page,segment):
            self.api.cancel_spotlight_video(suppress=True)
            self.api.prop('SelectedPage', '')
            saved = window.getProperty('NF.HeroData')
            self.api.publish(json.loads(saved) if saved else {})
            if saved:
                self.api.prop('SelectedPage', page)
            self.active = page
            self.active_window = (page,segment)
            self.entered_at = time.monotonic()
            self.next_freshness_check = 0
            self.first_visible = False
            self.api.prop('SpotlightCaption', 'SERIEN FÜR DICH' if page=='tv' else
                          'FILME FÜR DICH' if page=='movie' else 'FÜR DICH · FILME & SERIEN')
            try:
                requested = float(self.api.prop('HubRequestedAt') or self.entered_at)
                if self.entered_at-requested > 30:
                    requested=self.entered_at
            except ValueError:
                requested = self.entered_at
            self.requested_at = requested
            load_timing.record('skin','hub.enter',requested,hub=page,restored=bool(saved))
            self.visited.add(page)
        for offset, row in enumerate(tab.get('rows', [])[:48]):
            cid = 600+offset
            hub_protocol.set_property(window,'NF.RowHeading'+str(cid), row['label'])
            kind = 'continue' if row['path'].startswith('nf://continue') else 'top10' if row['label'].lower().startswith('top 10') else 'content'
            hub_protocol.set_property(window,'NF.RowKind'+str(cid), kind)
        return tab

    def tick(self):
        if self.api.prop('StartupBlocked')=='true' or xbmc.getCondVisibility('Player.HasVideo'):return False
        if (xbmc.getCondVisibility('System.HasModalDialog')
                or xbmc.getCondVisibility('System.ScreenSaverActive')
                or xbmc.getCondVisibility('System.DPMSActive')):
            return False
        self.apply_spotlight_setting()
        self.restore_detail_focus()
        self.complete_tasks()
        self.budget.sample()
        self.api.prop('PerformanceProfile', self.budget.name)
        self.api.prop('DirectSlotCount', str(len(self.config['tabs'])))
        if self.layout_stamp != self.api.prop('LayoutChanged'):
            self.load_config()
        active_id=xbmcgui.getCurrentWindowId()
        window = xbmcgui.Window(active_id)
        raw_slot = window.getProperty('NF.HubSlot')
        if not raw_slot.isdigit():
            return False
        tab = self.enter(window, int(raw_slot))
        if tab is None:
            return False
        page = tab['id']
        rows = tab.get('rows', [])[:48]
        revision=self.api.prop('TVRevision')
        if revision!=self.tv_revision:
            self.tv_revision=revision
            for tvtab in self.config['tabs']:
                for index,row in enumerate(tvtab.get('rows',[])[:48]):
                    if row.get('path','').startswith(('nf://nebelflix/tv','nf://moviescout/germany','plugin://plugin.video.nebelflix.tv/')):
                        identity=(tvtab['id'],600+index)
                        hub_protocol.set_property(self.home,direct_widgets.key(*identity)+'.Stale','true')
                        self.refreshed.discard(identity)
        start = int(window.getProperty('NF.Segment') or 0)*ROWS_PER_VIEW
        end = min(len(rows), start+ROWS_PER_VIEW)
        bound = int(window.getProperty('NF.Bound') or start)
        # Restore every saved row before waiting for any network result. A bound
        # count alone cannot represent this sparse set of already restored rows.
        for index in range(start,end):
            cid=600+index
            if (self.enabled(rows[index]) and not window.getProperty('NF.RowURL'+str(cid)) and not window.getProperty('NF.RowNative'+str(cid))
                    and direct_widgets.cached(rows[index]['path']) is not None):
                self.bind(window,page,cid,rows[index])
        bound=end
        hub_protocol.set_property(window,'NF.Bound',str(end))

        # Fill only a short navigation horizon, one provider at a time on sticks.
        try:
            focus = window.getFocusId()
        except RuntimeError:
            focus = 0
        horizon = start+self.budget.limits['initial_rows']
        if 600 <= focus < 648:
            horizon = max(horizon, focus-600+3)
        # Continue preparing this hub after the first visible rows, within the
        # same request budget. Never stop permanently at the initial horizon.
        if not self.budget.pressure:
            horizon = max(horizon, bound+1)
        active_jobs = 0
        visible_count = 0
        visible_after_focus = 0
        for index in range(start,min(bound,len(rows))):
            cid = 600+index
            if not self.enabled(rows[index]):
                self.jobs.discard((page,cid))
                continue
            prefix = direct_widgets.key(page, cid)
            state = self.home.getProperty(prefix+'.State')
            has_items = int(window.getProperty('NF.TileCount'+str(cid)) or 0)>0 or int(xbmc.getInfoLabel('Container(%s).NumItems'%cid) or 0)>0
            if (state=='queued' and has_items and not self.home.getProperty(prefix+'.Job')
                    and not xbmc.getCondVisibility('Container(%s).IsUpdating'%cid)):
                # Kodi served the stable URL from its native directory cache;
                # no Python provider needs to run merely to display these items.
                state='ready'
                hub_protocol.set_property(self.home,prefix+'.State',state)
                entry=direct_widgets.cached(rows[index]['path'])
                if entry and time.time()-entry.get('time',0) >= library.widget_cache_ttl(rows[index]['path']):
                    hub_protocol.set_property(self.home,prefix+'.Stale','true')
            pending = state in ('queued','loading')
            if state == 'loading':
                try:
                    pending = time.time()-float(self.home.getProperty(prefix+'.Started')) < 90
                except ValueError:
                    pending = False
            # Restoring saved content never blocks unrelated rows.
            active_jobs += int(pending and window.getProperty('NF.RowNetwork'+str(cid))=='true')
            if pending:
                self.jobs.add((page,cid))
            if not pending:
                self.jobs.discard((page,cid))
                position=window.getProperty('NF.RestorePosition'+str(cid))
                if position.isdigit() and has_items and time.monotonic()>=float(window.getProperty('NF.RestoreAfter'+str(cid)) or 0):
                    try:window.getControl(cid).selectItem(int(position))
                    except RuntimeError:pass
                    window.clearProperty('NF.RestorePosition'+str(cid))
            hub_protocol.set_property(window,'NF.RowLoading'+str(cid), 'true' if pending else '')
            if state == 'error':
                hub_protocol.set_property(window,'NF.RowState'+str(cid), 'Quelle nicht erreichbar · Erneut versuchen')
            else:
                hub_protocol.set_property(window,'NF.RowState'+str(cid), '')
            visible_count += int(has_items)
            visible_after_focus += int(has_items and cid > focus)
        # Empty initial rows must not prevent discovery of later nonempty rows.
        if not visible_count and not active_jobs and bound < end:
            horizon = max(horizon, bound+1)
        if 600 <= focus < 648 and not visible_after_focus and not active_jobs and bound < end:
            horizon = max(horizon, bound+1)
        settled=all(not self.enabled(rows[i]) or self.home.getProperty(direct_widgets.key(page,600+i)+'.State') in ('ready','empty','error') for i in range(start,end))
        hub_protocol.set_property(window,'NF.Empty', 'true' if not visible_count and not active_jobs and settled else '')
        hub_protocol.set_property(window,'NF.RowsReady','true')
        # An empty/removed row cannot retain remote focus on an invisible control.
        if 600 <= focus < 648 and not active_jobs and not xbmc.getCondVisibility('Control.IsVisible(%s)'%focus):
            window.setFocusId(hub_protocol.topbar_id(int(raw_slot)))

        if visible_count and not self.first_visible:
            self.first_visible = True
            load_timing.record('skin','hub.first_visible',self.requested_at,hub=page)
        available=max(0,self.budget.limits['requests']-active_jobs)
        for index in range(start,end):
            cid=600+index
            if available<=0:break
            if self.enabled(rows[index]) and not window.getProperty('NF.RowURL'+str(cid)) and not window.getProperty('NF.RowNative'+str(cid)):
                self.bind(window,page,cid,rows[index]);available-=1
        self.navigate(window, page, rows)
        if window.getProperty('NF.PersonalRevision') != self.api.prop('RefreshWidgets'):
            hub_protocol.set_property(window,'NF.PersonalRevision', self.api.prop('RefreshWidgets'))
            for i in range(start,min(bound,len(rows))):
                row=rows[i]
                if row['path'].startswith(('nf://continue','nf://mylist')):
                    self.bind(window,page,600+i,row,refresh=True)
        # Cached rows appear immediately. Refresh an expired row once per visit,
        # after the visible initial work; Kodi retains its current list meanwhile.
        if not active_jobs and time.monotonic() >= self.next_freshness_check:
            self.next_freshness_check=time.monotonic()+60
            for i in range(start,min(bound,len(rows))):
                current=json.loads(window.getProperty('NF.Source'+str(600+i)) or '{}')
                source=current.get('source',rows[i]['path'])
                if source.startswith(('nf://continue','nf://mylist')):continue
                entry=direct_widgets.cached(source)
                if entry and time.time()-entry.get('time',0) >= library.widget_cache_ttl(source):
                    hub_protocol.set_property(self.home,direct_widgets.key(page,600+i)+'.Stale','true')
                    self.refreshed.discard((page,600+i))
        if not active_jobs and bound >= end and time.monotonic()-self.entered_at > 3 and not self.budget.pressure:
            for i in range(start,min(bound,len(rows))):
                row=rows[i]
                identity = (page,600+i)
                if identity not in self.refreshed and self.home.getProperty(direct_widgets.key(*identity)+'.Stale') == 'true':
                    self.refreshed.add(identity)
                    current=json.loads(window.getProperty('NF.Source'+str(600+i)))
                    history=json.loads(window.getProperty('NF.History'+str(600+i)) or '[]')
                    self.bind(window,page,600+i,row,source=current['source'],offset=current['offset'],back=bool(history),refresh=True)
                    break
        if xbmcgui.getCurrentWindowId()!=active_id:return False
        if time.monotonic()-self.entered_at>3:
            self.tick_logos(window,focus)
        item = self.api.update_spotlight(page) or {}
        if item:
            hub_protocol.set_property(window,'NF.HeroData', json.dumps(item,ensure_ascii=False))
        return True

    def navigate(self, window, page, rows):
        raw = self.api.prop('DirectNavigate') or self.api.prop('BrowseWidget')
        if not raw:
            return
        self.api.prop('DirectNavigate',''); self.api.prop('BrowseWidget','')
        data = json.loads(raw)
        cid = int(data.get('Row',data.get('row',0)))
        if not 600 <= cid < 600+len(rows) or data.get('Hub',page) != page:
            return
        history = json.loads(window.getProperty('NF.History'+str(cid)) or '[]')
        if data.get('DirectBack') or data.get('back'):
            if not history:
                return
            target = history.pop()
        else:
            target = dict(source=data.get('DirectNavigate') or data.get('path'),offset=data.get('Offset',0))
            if not target['source']:
                return
            if not data.get('Retry'):
                history.append(json.loads(window.getProperty('NF.Source'+str(cid))))
        hub_protocol.set_property(window,'NF.History'+str(cid),json.dumps(history))
        self.bind(window,page,cid,rows[cid-600],source=target['source'],offset=target['offset'],back=bool(history),refresh=bool(data.get('Retry')))
        task=self.tasks.get((page,cid))
        if task:self.explicit_requests[(page,cid)]=task[5]

    def busy(self):
        if any(not job.done() for job in self.inflight):return True
        for page,cid in self.jobs:
            prefix = direct_widgets.key(page,cid)
            state = self.home.getProperty(prefix+'.State')
            if self.home.getProperty(prefix+'.Job'):
                try:
                    if time.time()-float(self.home.getProperty(prefix+'.Started')) < 90:
                        return True
                except ValueError:
                    continue
        return False


def startup_setup_required(reset_requested=False):
    if reset_requested or not library.read('hub-setup.json',{}).get('completed'):
        return True
    # Also recognize an externally emptied discovery cache without a reset marker.
    from hub_preload import plan
    rows=plan(library.layout())
    return bool(rows) and all(direct_widgets.cached(row['source']) is None for row in rows)


def monitor(api):
    if api.prop('SetupActive') == 'true':
        return
    try:
        if time.time()-float(api.prop('Heartbeat') or 0) < 4:
            return
    except ValueError:
        pass
    api.prop('Heartbeat',time.time())
    api.prop('ControllerStarting','')
    api.prop('DirectHubs','true')
    reset_requested=(library.ROOT / library.WIDGET_RESET_MARKER).exists()
    if reset_requested:
        # An explicit user-requested cache reset, never a version migration.
        direct = library.ROOT/'direct-widgets'
        if direct.is_dir() and not direct.is_symlink():
            import re
            for item in direct.iterdir():
                if item.is_file() and not item.is_symlink() and re.fullmatch(r'[a-f0-9]{64}\.json',item.name):
                    item.unlink()
        library.apply_widget_cache_reset(rpc=api.rpc)
    for name in ('HubSwitching','PrepareWidgets','BackgroundCacheNotice','StopController'):
        api.prop(name,'')
    first = startup_setup_required(reset_requested)
    if first:api.prop('StartupBlocked','true')
    if first:
        library.request_hub_setup()
        api.prop('SetupActive','true')
        try:
            if not setup():return
        finally:
            api.prop('SetupActive','')
    import hub_preload
    force_preparation=reset_requested or api.prop('FullPreparationRequested')=='true'
    api.prop('FullPreparationRequested','')
    if hub_preload.run(api,force=force_preparation):return
    state=library.read(hub_preload.STATE,{})
    # Incomplete sources must not terminate the controller and lock the surface.
    # Normal row workers retry missing/stale data while available hubs stay usable.
    if api.MONITOR.abortRequested():return
    api.prop('StartupBlocked','')
    hubs = Hubs(api)
    progress = library.Progress(api.rpc,api.PLAYER,api.prop)
    revisions = revisions_updater.Updater(api.WIN,api.MONITOR,blocked=first)
    video = api.SpotlightVideo()
    manual = ManualTrailer(api)
    reload_skin = False
    try:
        while not api.MONITOR.waitForAbort(0.25) and xbmc.getSkinDir()=='skin.nebelflix':
            api.prop('Heartbeat',time.time())
            if api.prop('StopController')=='true':
                break
            try:
                if api.prop('FullPreparationRequested')=='true' and not api.PLAYER.isPlaying():
                    hubs.complete_tasks()
                    if hubs.busy():continue
                    api.prop('FullPreparationRequested','')
                    if hub_preload.run(api,force=True):break
                    if library.read(hub_preload.STATE,{}).get('completed_at'):
                        api.prop('StartupBlocked','')
                        hubs.pool.shutdown(wait=False);hubs.logo_pool.shutdown(wait=False)
                        hubs=Hubs(api)
                    continue
                pending=api.prop('PendingAction')
                if pending and api.prop('Revisions.Running')!='true':
                    api.prop('PendingAction','')
                    request=json.loads(pending)
                    if request.get('command') in ('details','trailer','play','mylist') and time.time()-request.get('at',0)<60:
                        executed=time.monotonic()
                        try:api.dispatch(request['command'],request.get('args',[]))
                        finally:load_timing.record('skin','action.execute.'+request['command'],executed,queued_ms=round((time.time()-request.get('at',time.time()))*1000))
                progress.tick()
                active = hubs.tick()
                if api.prop('StopSpotlight')=='true':
                    api.prop('StopSpotlight',''); api.cancel_spotlight_video(suppress=True)
                if api.prop('ResumeFallbackRequested')=='true':
                    api.resume_helper_fallback()
                if active or (api.prop('PlaybackOwner')=='auto' and api.PLAYER.isPlayingVideo()):
                    # Pausing widget work must not itself cancel an owned preview.
                    video.tick(hubs.active,api.action_item())
                else:
                    video.reset()
                manual.tick()
                if (revisions.due() and active and not hubs.busy()
                        and not xbmc.getCondVisibility('System.HasModalDialog')
                        and not api.PLAYER.isPlaying() and revisions.prepare()):
                    api.prop('Direct.Paused','true')
                    try:
                        if hubs.busy():
                            continue
                        if revisions.run():
                            reload_skin=True
                            break
                    finally:
                        api.prop('Direct.Paused','')
            except Exception as exc:
                xbmc.log('[Nebelflix Hubs] '+type(exc).__name__+': '+str(exc),xbmc.LOGERROR)
    finally:
        manual.close()
        hubs.pool.shutdown(wait=False)
        hubs.logo_pool.shutdown(wait=False)
        video.reset()
        api.prop('Heartbeat',''); api.prop('StopController',''); api.prop('Direct.Paused','')
        if reload_skin:
            xbmc.executebuiltin('ReloadSkin')
