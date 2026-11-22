"""A single Kodi provider pass per widget; no controller/snapshot round trip."""
import hashlib
import json
from pathlib import Path
import time
import uuid
from urllib.parse import urlencode

import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs
import load_timing
import performance
import widget_snapshot
import hub_protocol
import logo_art

ROOT=Path(xbmcvfs.translatePath('special://profile/addon_data/skin.nebelflix/'))


def read(name,default):
    for candidate in (ROOT/name,ROOT/(name+'.bak')):
        try:return json.loads(candidate.read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
    return default


def key(page, row):
    return 'NF.Direct.%s.%s' % (page, row)


def url(page, row, source, top10=False, refresh='', offset=0, back=False, request=''):
    return hub_protocol.url(page,row,source,top10,refresh,offset,back,request)


def rpc(method, params=None):
    response = json.loads(xbmc.executeJSONRPC(json.dumps(dict(
        jsonrpc='2.0', id=1, method=method, params=params or {}))))
    if 'error' in response:
        raise RuntimeError('Kodi-Datenquelle derzeit nicht erreichbar')
    return response.get('result', {})


def cache_name(source):
    return 'direct-widgets/' + hashlib.sha256(source.encode('utf-8')).hexdigest() + '.json'


def current_tv_cards(source, entry):
    """Retire old category shortcuts without discarding cached channel data."""
    if source not in ('nf://nebelflix/tv', 'nf://moviescout/germany') or not isinstance(entry, dict):
        return entry
    retired = {'plugin://plugin.video.nebelflix.tv/?action=list&group=' + group
               for group in ('favorites', 'recent')}
    return dict(entry, items=[item for item in entry.get('items', [])
                             if item.get('Path') not in retired])


def cached(source):
    entry = read(cache_name(source), None)
    if entry is not None:
        return current_tv_cards(source, entry)
    # Reuse users' already downloaded results when upgrading from 0.5.74.
    legacy = read('widget-cache.json', {}).get(source)
    if isinstance(legacy, dict):
        try:
            store(source, legacy)
        except OSError:
            pass  # A cache write must never hide already available content.
    return current_tv_cards(source, legacy) if isinstance(legacy, dict) else None


def store(source, entry):
    # One file per source prevents concurrent widgets overwriting each other.
    target = ROOT / cache_name(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(entry, ensure_ascii=False), encoding='utf-8')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def contents(source, force=False, paused=False):
    entry = cached(source)
    ttl = hub_protocol.widget_ttl(source)
    personal = source.startswith(('nf://continue', 'nf://mylist'))
    if entry and not personal and not force:
        return entry, time.time() - entry.get('time', 0) >= ttl, 'cache'
    if paused:
        return entry or dict(items=[], next='', time=0), False, 'paused'
    try:
        import library  # Network/provider helpers are unnecessary for cached lists.
        items, next_path = library.directory(rpc, source)
        entry = dict(items=items, next=next_path, time=time.time())
        if not personal:
            store(source, entry)
        return entry, False, 'fresh'
    except Exception:
        if entry:
            return entry, True, 'stale'
        raise


def restore(window, home, page, cid, source, entry, top10=False, allow_personal=False, offset=0, back=False, budget=None):
    """Fill the native list from disk without launching a Python provider per row."""
    entry = current_tv_cards(source, entry)
    if not allow_personal and source.startswith(('nf://continue','nf://mylist')):
        return False
    try:
        previous=json.loads(window.getProperty('NF.Source'+str(cid)) or '{}')
        selected=max(0,window.getControl(cid).getSelectedPosition())
        old_item=json.loads(window.getProperty('NF.Tile%s.%s.NF.Data'%(cid,selected)) or '{}')
    except (RuntimeError,AttributeError,ValueError):
        previous={};selected=0;old_item={}
    preserve=previous.get('source')==source and previous.get('offset',0)==offset
    def identity(item):
        if item.get('TMDB'):return ('tmdb',item.get('Type',''),str(item['TMDB']))
        if item.get('Path'):return ('path',item['Path'])
        return ('title',item.get('Title',''))
    budget=budget or performance.Budget(read('performance.json',{}).get('profile','auto'))
    limit=10 if top10 else (12 if budget.name=='low' else 20)
    items=[dict(item,Hub=page,Row=cid) for item in entry.get('items',[])[offset:offset+limit]]
    for item in items:
        if not item.get('Clearlogo'):
            logo=logo_art.cached(item)
            if logo:item['Clearlogo']=logo.get('logo','')
        for field in ('Fanart','Poster','Clearlogo'):
            value=budget.image(item.get(field,''),field,tile=True)
            if value.startswith(('https://','http://')):
                from urllib.parse import urlparse
                suffix=Path(urlparse(value).path).suffix.lower()
                if suffix not in ('.jpg','.jpeg','.png','.webp'):suffix='.jpg'
                local=ROOT/'artwork'/(hashlib.sha256(value.encode('utf-8')).hexdigest()+suffix)
                if local.is_file():value=str(local)
            item[field]=value
    if not top10 and (len(entry.get('items',[]))>offset+limit or entry.get('next')):
        local=len(entry.get('items',[]))>offset+limit
        target=source if local else entry['next']
        items.append(dict(Title='Weiter',Path=target,Folder=True,DirectNavigate=target,
                          Offset=offset+limit if local else 0,Hub=page,Row=cid))
    if back:
        items.insert(0,dict(Title='Zurück',Path='nf://back',DirectBack=True,Row=cid,Hub=page))
    # A native container without a list provider ignores XML onclick actions.
    # Populate property-backed static items: Kodi owns selection and clicks,
    # while cached rows still require no individual Python provider invocation.
    for index,item in enumerate(items[:hub_protocol.STATIC_TILE_LIMIT]):
        prefix='NF.Tile%s.%s.'%(cid,index)
        hub_protocol.set_property(window,prefix+'Title',item.get('Title',''))
        hub_protocol.set_property(window,prefix+'Thumb',item.get('Fanart') or item.get('Poster') or 'special://skin/media/nebelflix/poster.png')
        values={'NF.Data':json.dumps(item,ensure_ascii=False),'NF.Hub':page,
                'NF.Clearlogo':item.get('Clearlogo',''),
                'NF.RankArt':'special://skin/media/nebelflix/rank-%s.png'%(index+1) if top10 and index<10 else '',
                'NFTV.Category':'1' if item.get('GuideOpen') or (item.get('AddonBrowse') and '?action=list&group=' in item.get('Path','')) else '',
                'tmdb_id':str(item.get('TMDB','')),'tmdb_type':item.get('Type','')}
        for field in ('EPG','EPGNext','EPGProgress'):values['NF.'+field]=str(item.get(field) or '')
        for field in hub_protocol.STATIC_TILE_PROPERTIES:hub_protocol.set_property(window,prefix+field,values.get(field,''))
    hub_protocol.set_property(window,'NF.TileCount'+str(cid),str(min(len(items),hub_protocol.STATIC_TILE_LIMIT)))
    hub_protocol.set_property(window,'NF.RowNative'+str(cid),'true')
    hub_protocol.set_property(window,'NF.RowLoading'+str(cid),'')
    hub_protocol.set_property(window,'NF.Source'+str(cid),json.dumps(dict(source=source,offset=offset)))
    prefix=key(page,cid)
    hub_protocol.set_property(home,prefix+'.State','ready' if items else 'empty')
    hub_protocol.set_property(home,prefix+'.Stale','true' if time.time()-entry.get('time',0)>=hub_protocol.widget_ttl(source) else '')
    if preserve and items:
        target=next((i for i,item in enumerate(items) if identity(item)==identity(old_item)),min(selected,len(items)-1))
        hub_protocol.set_property(window,'NF.RestorePosition'+str(cid),str(target))
        hub_protocol.set_property(window,'NF.RestoreAfter'+str(cid),str(time.monotonic()+0.2))

    return True


def run(handle, params):
    page = params.get('page', ['home'])[0]
    row = int(params.get('row', ['600'])[0])
    source = params.get('source', [''])[0]
    if not 600 <= row < 648 or not source:
        raise ValueError('Invalid direct widget')
    home = xbmcgui.Window(10000)
    prop = key(page, row)
    request = params.get('request', [''])[0]
    token = uuid.uuid4().hex
    started = time.monotonic()
    owns_request = lambda: not request or home.getProperty(prop + '.Request') == request
    if owns_request():
        home.setProperty(prop + '.Job', token)
        home.setProperty(prop + '.Started', str(time.time()))
        home.setProperty(prop + '.State', 'loading')
    status = 'error'
    items = []
    stale = False
    state = 'error'
    try:
        entry, stale, status = contents(source, bool(params.get('refresh', [''])[0]),
                                       home.getProperty('NF.Direct.Paused') == 'true')
        top10 = params.get('top10', ['0'])[0] == '1'
        budget = performance.Budget(read('performance.json', {}).get('profile', 'auto'))
        limit = 10 if top10 else (12 if budget.name == 'low' else 20)
        offset = max(0, int(params.get('offset', ['0'])[0]))
        items = [dict(item, Hub=page, Row=row) for item in entry.get('items', [])[offset:offset+limit]]
        for item in items:
            for field in ('Fanart', 'Poster', 'Clearlogo'):
                item[field] = budget.image(item.get(field, ''), field, tile=True)
        next_path = entry.get('next')
        if not top10 and len(entry.get('items', [])) > offset+limit:
            items.append(dict(Title='Weiter', Path=source, Folder=True,
                              DirectNavigate=source, Offset=offset+limit, Row=row, Hub=page))
        elif next_path and not top10:
            items.append(dict(Title='Weiter', Path=next_path, Folder=True,
                              DirectNavigate=next_path, Row=row, Hub=page))
        if params.get('back', ['0'])[0] == '1':
            items.insert(0, dict(Title='Zurück', Path='nf://back', DirectBack=True, Row=row, Hub=page))
        xbmcplugin.setContent(handle, 'videos')
        entries = [(item.get('Path') or 'nf://empty',
                    widget_snapshot.listitem(item, rank, top10, source.startswith('nf://continue')),
                    bool(item.get('Folder'))) for rank, item in enumerate(items, 1)]
        xbmcplugin.addDirectoryItems(handle, entries, len(entries))
        xbmcplugin.endOfDirectory(handle, cacheToDisc=bool(items) and not source.startswith(('nf://continue','nf://mylist')))
        state = 'ready' if items else 'empty'
    except Exception as exc:
        xbmc.log('[Nebelflix Widgets] row=%s %s' % (row, type(exc).__name__), xbmc.LOGWARNING)
        # A retry tile belongs to this row; no modal/global loading screen.
        retry = dict(Title='Quelle nicht erreichbar · Erneut versuchen', Path=source,
                     DirectNavigate=source, Retry=True, Row=row, Hub=page)
        xbmcplugin.addDirectoryItem(handle, source, widget_snapshot.listitem(retry), False)
        xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    finally:
        if owns_request():
            home.setProperty(prop + '.State', state)
            home.setProperty(prop + '.Stale', 'true' if stale else '')
        if home.getProperty(prop + '.Job') == token:
            home.clearProperty(prop + '.Job')
        load_timing.record('skin', 'widget.direct', started, hub=page, row=row,
                           items=len(items), status=status)
