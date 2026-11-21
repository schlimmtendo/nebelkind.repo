"""Complete, resumable discovery preparation after an explicit cache reset."""
import time
import xbmc
import xbmcgui
import library
import direct_widgets
import performance
from artwork import Artwork
from bounded_workers import SearchExecutor

STATE='hub-preparation.json'


def plan(config):
    seen=set();rows=[]
    for tab in config.get('tabs',[]):
        for row in tab.get('rows',[])[:48]:
            source=row.get('path','')
            if not source or source in seen or source.startswith(('nf://continue','nf://mylist')):continue
            seen.add(source);rows.append(dict(source=source,hub=tab.get('label',''),label=row.get('label','')))
    return rows


def required(rows,force=False):
    if not rows:return False
    missing=sum(direct_widgets.cached(row['source']) is None for row in rows)
    state=library.read(STATE,{})
    if force:return True
    if not missing:return False
    return not state.get('completed_at') or missing>=max(1,(len(rows)+1)//2)


def run(api,force=False):
    """Return true only when a supported application restart was requested."""
    rows=plan(library.layout())
    if not required(rows,force):return False
    api.prop('Revisions.Deferred','true')
    api.prop('StartupBlocked','true')
    budget=performance.Budget(library.read('performance.json',{}).get('profile','auto'))
    pool=SearchExecutor(max_workers=budget.limits['requests'])
    art=Artwork(library.ROOT/'artwork',max_jobs=budget.limits['artwork_jobs'],max_mb=budget.limits['artwork_mb'])
    jobs={};queue=list(rows);done=0;failed=0;cancelled=False
    library.write(STATE,dict(started_at=time.time(),completed_at=0,planned=len(rows)))
    api.prop('PrepareWidgets','true');api.prop('SkipPreload','')
    api.prop('PreloadStatus','Alle Hubs werden vorbereitet ...')
    api.publish_preload_progress(0,len(rows))
    xbmc.executebuiltin('ActivateWindow(1194)')
    try:
        while queue or jobs:
            api.prop('Heartbeat',time.time())
            if api.MONITOR.waitForAbort(0.1) or api.prop('SkipPreload')=='true':
                cancelled=True;break
            while queue and len(jobs)<budget.limits['requests']:
                row=queue.pop(0)
                future=pool.submit(direct_widgets.contents,row['source'])
                jobs[future]=(row,time.monotonic())
            for future,(row,started) in list(jobs.items()):
                if not future.done() and time.monotonic()-started<90:continue
                del jobs[future];done+=1
                try:
                    if not future.done():
                        future.cancel();raise TimeoutError('Widget preparation timeout')
                    entry,stale,status=future.result()
                    if status in ('error','paused','stale'):raise RuntimeError('Source not current')
                    # Empty responses are valid; they are still persisted by contents.
                    art.tick()
                    tiles=[dict(item,**{field:budget.image(item.get(field,''),field,tile=True)
                        for field in ('Fanart','Poster','Clearlogo')})
                        for item in entry.get('items',[])[:budget.limits['artwork_items']]]
                    art.prepare(tiles)
                except Exception as exc:
                    failed+=1
                    xbmc.log('[Nebelflix Preparation] '+type(exc).__name__,xbmc.LOGWARNING)
                api.prop('PreloadStatus',row['hub']+' - '+row['label'])
                api.publish_preload_progress(done,len(rows))
        # Finish downloads already queued for first visible artwork.
        artwork_deadline=time.monotonic()+8
        while not cancelled and art.jobs and time.monotonic()<artwork_deadline:
            api.prop('Heartbeat',time.time());art.tick()
            if api.MONITOR.waitForAbort(0.1) or api.prop('SkipPreload')=='true':cancelled=True
        complete=not cancelled and not failed and done==len(rows)
        state=dict(started_at=library.read(STATE,{}).get('started_at',0),planned=len(rows),
                   completed_at=time.time() if complete else 0,failed=failed,cancelled=cancelled)
        library.write(STATE,state)
        if not complete:
            if not cancelled:api.notification('%s Reihen vorbereitet, %s Quellen nicht erreichbar. Vorbereitung wird beim naechsten Start fortgesetzt.'%(done-failed,failed))
            return False
        # Persist completion BEFORE restarting. Ordinary launches never restart again.
        library.preload_finished(len(rows),0)
        supported=xbmc.getCondVisibility('System.Platform.Windows') or xbmc.getCondVisibility('System.Platform.Linux')
        android=xbmc.getCondVisibility('System.Platform.Android')
        if supported and not android:
            api.prop('PreloadStatus','Vorbereitung fertig - Kodi startet einmal neu ...')
            state['restart_requested_at']=time.time();library.write(STATE,state)
            if not api.MONITOR.waitForAbort(1):xbmc.executebuiltin('RestartApp')
            return True
        api.notification('Alle Hubs gespeichert. Bitte Kodi einmal beenden und erneut oeffnen.')
        return False
    finally:
        for future in jobs:future.cancel()
        pool.shutdown(wait=False);art.close()
        api.prop('PrepareWidgets','')
        xbmc.executebuiltin('Dialog.Close(1194)')
