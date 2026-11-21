"""Draw the menu/cached native lists before importing the Python controller."""
import json
import hashlib
import sys
import time
from pathlib import Path
import xbmc
import xbmcgui
import xbmcvfs
import hub_protocol


def cached_hero(root,tab):
    legacy=None
    for row in tab.get('rows',[])[:4]:
        source=row['path']
        filename=hashlib.sha256(source.encode('utf-8')).hexdigest()+'.json'
        try:
            entry=json.loads((root/'direct-widgets'/filename).read_text(encoding='utf-8'))
        except (OSError,ValueError):
            if legacy is None:
                try:legacy=json.loads((root/'widget-cache.json').read_text(encoding='utf-8'))
                except (OSError,ValueError):legacy={}
            entry=legacy.get(source,{})
        item=next((i for i in entry.get('items',[]) if i.get('TMDB') and i.get('Type') in ('movie','tv')),None)
        if item:return item
    return None


def configure(window,home,root,tab,slot=0,segment=0):
    started=time.monotonic()
    restored=0
    page=tab['id']; start=segment*hub_protocol.ROWS_PER_VIEW
    window.setProperty('NF.Page',page)
    window.setProperty('NF.HubSlot',str(slot));window.setProperty('NF.Segment',str(segment))
    logical=hub_protocol.window_id(slot,segment)
    home.setProperty('NF.TabWindow'+str(slot),'Home' if logical==10000 else str(logical))
    window.setProperty('NF.NextWidgetsWindow',str(hub_protocol.window_id(slot,min(5,segment+1))))
    previous=hub_protocol.window_id(slot,max(0,segment-1))
    window.setProperty('NF.PreviousWidgetsWindow','Home' if previous==10000 else str(previous))
    if window.getProperty('NF.LayoutSignature')!=hub_protocol.signature(tab,segment):
        window.setProperty('NF.RowsReady','')
        count=start
        cache_budget=None
        try:legacy=json.loads((root/'widget-cache.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):legacy={}
        for i in range(start,min(start+hub_protocol.ROWS_PER_VIEW,len(tab.get('rows',[])))):
            row=tab['rows'][i];cid=600+i;source=row['path'];top10=row['label'].lower().startswith('top 10')
            kind='continue' if source.startswith('nf://continue') else 'top10' if top10 else 'content'
            window.setProperty('NF.RowHeading'+str(cid),row['label'])
            window.setProperty('NF.RowKind'+str(cid),kind)
            hidden=(kind=='continue' and xbmc.getCondVisibility('Skin.HasSetting(NFHideContinue)')) or (top10 and xbmc.getCondVisibility('Skin.HasSetting(NFHideTop10)'))
            # Bind saved rows immediately, independently of slow uncached sources.
            filename=hashlib.sha256(source.encode('utf-8')).hexdigest()+'.json'
            saved=(root/'direct-widgets'/filename).is_file() or source in legacy
            if not hidden and saved:
                import direct_widgets
                if cache_budget is None:cache_budget=direct_widgets.performance.Budget(direct_widgets.read('performance.json',{}).get('profile','auto'))
                entry=direct_widgets.cached(source)
                if entry is not None and direct_widgets.restore(window,home,page,cid,source,entry,top10,budget=cache_budget):
                    restored+=1
                    count=i+1
                    continue
            if not hidden and (saved or i<start+2):
                window.setProperty('NF.RowNetwork'+str(cid),'' if saved else 'true')
                prefix='NF.Direct.%s.%s'%(page,cid)
                home.setProperty(prefix+'.Request',hub_protocol.request_key(page,cid,source))
                home.setProperty(prefix+'.State','queued')
                window.setProperty('NF.RowLoading'+str(cid),'true')
                window.setProperty('NF.Source'+str(cid),json.dumps(dict(source=source,offset=0)))
                # No provider invocation here: missing data is queued by the
                # persistent controller after its startup imports complete.
                home.setProperty(prefix+'.State','waiting')
            count=i+1
        window.setProperty('NF.Bound',str(count))
        window.setProperty('NF.LayoutSignature',hub_protocol.signature(tab,segment))
        window.setProperty('NF.RowsReady','true')
    import load_timing
    load_timing.record('skin','hub.restore',started,hub=page,rows=restored)
    hero=json.loads(window.getProperty('NF.HeroData') or 'null') or cached_hero(root,tab)
    if hero:window.setProperty('NF.HeroData',json.dumps(hero,ensure_ascii=False))
    window.clearProperty('NF.Empty')
    expected=logical if logical==10000 else logical+10000
    if xbmcgui.getCurrentWindowId()!=expected:
        return  # A late cache restore may finish, but cannot replace another hub's hero.
    home.setProperty('NF.Page',page)
    home.setProperty('NF.TabSelected',str(slot))
    window.setProperty('NF.HasPreviousWidgets','true' if segment else '')
    window.setProperty('NF.HasMoreWidgets','true' if start+hub_protocol.ROWS_PER_VIEW<len(tab.get('rows',[])) else '')
    home.setProperty('NF.SelectedPage','')
    home.setProperty('NF.HasHero','')
    if hero and segment==0 and page in ('home','movie','tv','new'):
        data=json.dumps(hero,ensure_ascii=False)
        window.setProperty('NF.HeroData',data);home.setProperty('NF.Item',data)
        for field in hub_protocol.METADATA_FIELDS:
            home.setProperty('NF.'+field,str(hero.get(field) or ''))
        home.setProperty('NF.Meta',' · '.join(str(x) for x in (
            'Serie' if hero.get('Type')=='tv' else 'Film',hero.get('Year'),hero.get('Genres')) if x))
        home.setProperty('NF.SpotlightCaption','SERIEN FÜR DICH' if page=='tv' else 'FILME FÜR DICH' if page=='movie' else 'FÜR DICH · FILME & SERIEN')
        home.setProperty('NF.SelectedPage',page);home.setProperty('NF.HasHero','true')


def ensure_monitor(home):
    if not home.getProperty('NF.Heartbeat') and not home.getProperty('NF.ControllerStarting'):
        home.setProperty('NF.ControllerStarting','true')
        xbmc.executebuiltin('RunScript(special://skin/resources/monitor_entry.py)')


def prewarm(home,root,config):
    for slot,tab in enumerate(config.get('tabs',[])[:12]):
        if slot==0:continue
        try:configure(xbmcgui.Window(hub_protocol.window_id(slot)+10000),home,root,tab,slot)
        except RuntimeError:xbmc.log('[Nebelflix Startup] Hub cache window unavailable',xbmc.LOGWARNING)


def startup_intro(home,prepare=None):
    def finish_prepare():
        if prepare:prepare()
    if home.getProperty('NF.StartupIntroPending')!='true':return finish_prepare()
    home.clearProperty('NF.StartupIntroPending')
    if xbmc.getCondVisibility('Skin.HasSetting(NFDisableIntro)'):return finish_prepare()
    clip=xbmcvfs.translatePath('special://skin/media/nebelflix/intro.mp4')
    player=xbmc.Player()
    if not xbmcvfs.exists(clip) or player.isPlaying():return finish_prepare()
    home.setProperty('NF.PlaybackOwner','intro')
    home.setProperty('NF.IntroActive','true');home.setProperty('NF.SkipIntro','false')
    monitor=xbmc.Monitor();started=time.monotonic()
    try:
        player.play(clip,windowed=True)
        finish_prepare()
        while not monitor.waitForAbort(0.1) and time.monotonic()-started<8:
            if home.getProperty('NF.SkipIntro')=='true':break
            if time.monotonic()-started>1 and not player.isPlaying():break
            try:home.setFocusId(1999)
            except RuntimeError:pass
    finally:
        home.setProperty('NF.IntroActive','false')
        if player.isPlaying():
            try:
                if player.getPlayingFile()==clip:player.stop()
            except RuntimeError:pass
        try:home.setFocusId(200)
        except RuntimeError:pass


def startup_phase(root,config):
    def saved(name):
        try:return json.loads((root/name).read_text(encoding='utf-8-sig'))
        except (OSError,ValueError):return {}
    if (root/'widget-cache-reset-request.json').exists() or not saved('hub-setup.json').get('completed'):
        return 'setup'
    paths={row['path'] for tab in config.get('tabs',[]) for row in tab.get('rows',[]) if not row['path'].startswith(('nf://continue','nf://mylist'))}
    legacy=saved('widget-cache.json')
    missing=sum(not (root/'direct-widgets'/(hashlib.sha256(path.encode('utf-8')).hexdigest()+'.json')).is_file() and path not in legacy for path in paths)
    if paths and missing==len(paths):return 'setup'
    preparation=saved('hub-preparation.json')
    if preparation and not preparation.get('completed_at'):return 'prepare'
    if missing and (not preparation.get('completed_at') or missing>=max(1,(len(paths)+1)//2)):return 'prepare'
    return 'ready'


def run(slot=None,segment=0):
    home=xbmcgui.Window(10000)
    root=Path(xbmcvfs.translatePath('special://profile/addon_data/skin.nebelflix/'))
    source=root/'layout.json'
    if not source.is_file():source=Path(__file__).resolve().parent/'default-layout.json'
    try:
        config=json.loads(source.read_text(encoding='utf-8-sig'))
        phase=startup_phase(root,config)
        if slot is None:
            home.setProperty('NF.StartupBlocked','true' if phase!='ready' else '')
        for index,tab in enumerate(config.get('tabs',[])[:12]):
            home.setProperty('NF.TabWindow'+str(index),'Home' if index==0 else str(hub_protocol.window_id(index)))
            home.setProperty('NF.TabLabel'+str(index),tab['label'])
            home.setProperty('NF.TabPage'+str(index),tab['id'])
        if slot is None and xbmcgui.getCurrentWindowId()!=10000:
            ensure_monitor(home)
            xbmc.executebuiltin('ReplaceWindow(Home)',True)
        if home.getProperty('NF.StartupBlocked')=='true':
            xbmc.executebuiltin('SetFocus(1899)')
            ensure_monitor(home)
            return
        if slot is None:configure(home,home,root,config['tabs'][0])
        else:
            window=xbmcgui.Window(xbmcgui.getCurrentWindowId())
            if window.getProperty('NF.HubSlot')!=str(slot) or window.getProperty('NF.Segment')!=str(segment):return
            configure(window,home,root,config['tabs'][slot],slot,segment)
        home.setProperty('NF.BootReady','true')
    except (OSError,ValueError,KeyError,IndexError,TypeError) as exc:
        xbmc.log('[Nebelflix Startup] '+type(exc).__name__,xbmc.LOGWARNING)
    ensure_monitor(home)
    if slot is None:startup_intro(home,lambda:prewarm(home,root,config))


if __name__=='__main__':
    run(int(sys.argv[2]),int(sys.argv[3])) if len(sys.argv)>3 and sys.argv[1]=='hub' else run()
