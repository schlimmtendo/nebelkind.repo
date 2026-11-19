"""Read-only Vavoo TV Plus channel discovery, grouping and playback URLs."""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs
import xbmcaddon
import xbmcvfs
from .common import DATA, canonical, clean, short_id, download, rpc, load_json, save_json, settings, log

VAVOO_ID = 'plugin.video.vavoo.tv.plus'
VAVOO_ROUTE = 'plugin://plugin.video.vavoo.tv.plus/?action=channels_flat&group=Germany'


def _vavoo_epg_ids():
    """Fallback ID map from supplied Vavoo TV Plus 2.0.8 (no network calls)."""
    path=Path(__file__).resolve().parents[1] / 'data' / 'vavoo-epg-ids.json'
    try:
        return json.loads(path.read_text(encoding='utf8'))
    except (OSError,ValueError,TypeError):
        return {}


VAVOO_EPG_IDS = _vavoo_epg_ids()


def _vavoo_tvg_id(name):
    return str(VAVOO_EPG_IDS.get(canonical(name), ''))




# Vavoo TV Plus 2.0.8: exact shipped group names and playlist stems.
# Custom groups in the installed Vavoo profile are also displayed.
VAVOO_DEFAULT_GROUPS = [
    ('tv_spielfilm_favoriten_hd', 'TV Spielfilm HD'),
    ('private', 'Private'), ('serien', 'Serien'), ('kids', 'Kids'),
    ('sky', 'Sky'), ('cinema_tv', 'Cinema TV'), ('cinedome', 'Cinedome'),
    ('sky_box', 'Sky Box Office'), ('bluetv', 'BlueTV'),
    ('popcorn', 'Popcorn TV'), ('sport', 'Sport'),
    ('bundesliga', 'Bundesliga'), ('dazn', 'DAZN'),
    ('pluto_tv', 'Pluto TV'), ('rakuten_tv', 'Rakuten TV'),
    ('doku', 'Doku'), ('news', 'News'), ('musik', 'Musik'),
]

def vavoo_group_catalog():
    """Mirror Vavoo's actual group names and ordering; never alter its profile."""
    root=Path(xbmcvfs.translatePath('special://profile/addon_data/plugin.video.vavoo.tv.plus/'))
    manifest=root/'groups.json'
    entries=[]
    try:
        saved=json.loads(manifest.read_text(encoding='utf8'))
        if isinstance(saved,list):
            for row in saved[:100]:
                if not isinstance(row,dict): continue
                stem=Path(str(row.get('file') or '')).name
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}\.m3u',stem,flags=re.I):continue
                label=clean(row.get('name'))[:70]
                if label: entries.append((stem[:-4],label))
    except (OSError,ValueError,TypeError):pass
    if not entries:entries=list(VAVOO_DEFAULT_GROUPS)
    result=[]
    seen=set()
    for stem,title in entries:
        slug='vavoo_'+stem.lower()
        if slug not in seen:
            result.append((slug,title)); seen.add(slug)
    return result

def group_catalog():
    return vavoo_group_catalog()

def _group_files(slug):
    """Vavoo profile overrides bundled playlist, but an empty stub is not data.

    Some upgrades create a zero-entry profile M3U for a newly introduced
    standard group while still shipping a populated M3U inside resources.
    Never delete, edit or replace Vavoo files; read packaged lists only as
    a fallback when the user's profile offers no playable entries.
    """
    if not slug.startswith('vavoo_'):
        return []
    stem=slug[len('vavoo_'):]
    if not re.fullmatch(r'[a-z0-9_-]{1,80}',stem):
        return []
    filename=stem+'.m3u'
    result=[Path(xbmcvfs.translatePath('special://profile/addon_data/plugin.video.vavoo.tv.plus/'))/'favgroups'/filename]
    try:
        installed=Path(xbmcvfs.translatePath(xbmcaddon.Addon(VAVOO_ID).getAddonInfo('path')))
        result.append(installed/'resources'/'favgroups'/filename)
    except Exception as exc:
        log('Vavoo Gruppenverzeichnis nicht verfügbar: %s' % exc)
    return [p for p in result if p.is_file()]


def _group_file(slug):
    paths=_group_files(slug)
    return paths[0] if paths else None


def _group_key(name):
    # Vavoo commonly uses HD+ as a source-quality tag, not part of a station.
    name=re.sub(r'(?i)\b(?:hd|fhd|uhd|sd)\s*\+', ' ',str(name or ''))
    name=re.sub(r'(?i)\s*\(\d{1,3}\)\s*$','',name)
    return canonical(name)

def _vavoo_group_records(slug):
    """Read Vavoo's real favourite group entries, including revised releases.

    A profile file containing no playable entries is not allowed to shadow a
    populated original in the installed add-on. Valid custom profile lists
    always win; source URLs are passed to Vavoo unmodified.
    """
    paths=_group_files(slug)
    for file in paths:
        entries=[];pending={}
        try:
            with file.open('r',encoding='utf-8-sig',errors='replace') as f:
                for line in f:
                    line=line.strip()
                    if line.startswith('#EXTINF:'):
                        attrs, _, title=line.partition(',')
                        pending={'name':clean(title),'tvg_id':_attr(attrs,'tvg-id'),
                                 'logo':_attr(attrs,'tvg-logo'),'group':slug}
                    elif pending and line and not line.startswith('#'):
                        u=urlsplit(line)
                        if u.scheme=='plugin' and u.netloc==VAVOO_ID:
                            action=parse_qs(u.query).get('action',[''])[0]
                            if action=='play' and pending['name']:
                                entries.append(dict(pending,url=line,source='Vavoo Plus'))
                        pending={}
                        if len(entries)>=2000:break
        except OSError as exc:
            log('Vavoo Gruppenliste %s: %s' % (file,exc))
        if entries:
            return entries
    if paths:
        log('Vavoo Gruppe %s: keine abspielbaren Einträge in %d Datei(en)' % (slug,len(paths)))
    return []


def _vavoo_group_channels(slug,allchannels):
    records=_vavoo_group_records(slug)
    if not records:return []
    by_key={ch.get('normalized') or _group_key(ch['name']):ch for ch in allchannels}
    selected=[];keys=set()
    for raw in records:
        key=_group_key(raw['name'])
        if not key or key in keys:continue
        keys.add(key)
        existing=by_key.get(key)
        if existing:
            ch=dict(existing)
            if not ch.get('tvg_id') and raw.get('tvg_id'):ch['tvg_id']=raw['tvg_id']
            if not ch.get('logo') and raw.get('logo'):ch['logo']=raw['logo']
        else:
            # A curated Vavoo group may contain stations absent from its Germany
            # country catalog. Keep those as optional groups-only channels.
            merged=_group_channels(_identify([raw]))
            if not merged:continue
            ch=merged[0]
        # A group may refer to a distinct Vavoo source not present in the
        # Germany catalog. Preserve it as an additional playback variant.
        u=raw.get('url','')
        if u and not any(v.get('url')==u for v in ch.get('variants',[])):
            ch['variants']=[dict(id=short_id('group:'+u),name=raw['name'],
                                 url=u,source='Vavoo Plus')] + list(ch.get('variants',[]))
            ch['url']=ch['variants'][0]['url']
        ch['vavoo_group']=slug
        selected.append(ch)
    return selected


def group_for(name):
    val = canonical(name)
    if re.search(r'(?i)\b(?:wdr|ndr|mdr|swr|br\s*fernsehen|hr\s*fernsehen|rbb|regional|hamburg\s*1|rheinmain\s*tv)\b', name):
        return 'regional'
    if any(x in val for x in ('kika','disney','nickelodeon','toggo','superrtl','cartoon','junior')):
        return 'kids'
    if any(x in val for x in ('eurosport','skyport','sport1','sportdigital','dazn','sportdeutschland','sport')):
        return 'sports'
    if any(x in val for x in ('n24','ntv','weltnachrichten','tagesschau24','phoenix','bildtv','euronews','dwdeutsch')):
        return 'news'
    if any(x in val for x in ('arte','3sat','natgeowild','natgeo','discovery','history','dmax','docubox','weltderwunder','zdfinfo')):
        return 'docs'
    if any(x in val for x in ('mtv','deluxe','musictv','viva','schlager','radio','musik')):
        return 'music'
    if val in ('daserste','zdf','zdfneo','zdfinfo','one','ardalpha','arte','3sat') or val.startswith(('ard', 'zdf')):
        return 'public'
    if val.startswith(('rtl','vox','sat1','prosieben','kabeleins','tele5','sixx','nitro')):
        return 'private'
    return 'other'


def _vavoo_cache_path(key):
    """Read Vavoo TV Plus' existing channel cache, without modifying its data."""
    profile = Path(xbmcvfs.translatePath('special://profile/addon_data/' + VAVOO_ID + '/'))
    filename = __import__('hashlib').md5(str(key).encode('utf-8')).hexdigest() + '.json'
    return profile / 'cache' / filename


def _vavoo_signature():
    try:
        ver = xbmcaddon.Addon(VAVOO_ID).getAddonInfo('version')
        if tuple(int(n) for n in re.findall(r'\d+', ver)[:3]) < (2, 0, 8):
            return 'vavoo-version-too-old:' + ver
        mtime = 0
        for key in ('geil_channels_Germany', 'geil_last_good_channels_Germany'):
            f = _vavoo_cache_path(key)
            if f.exists():
                mtime = max(mtime, int(f.stat().st_mtime))
        return '%s:%s' % (ver, mtime)
    except Exception:
        return 'not-installed'


def _vavoo_from_cache():
    """Use local, provider-maintained catalog; keep every stream as an alternative.

    Cache data belong to Vavoo Plus 2.0.8 and are never edited by Nebelflix.
    The retained last-good snapshot is valid if a refresh fails temporarily.
    """
    for key in ('geil_channels_Germany', 'geil_last_good_channels_Germany'):
        f = _vavoo_cache_path(key)
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            expiry = float(data.get('expires') or 0)
            if expiry and expiry < time.time():
                continue
            catalog = data.get('value')
            if not isinstance(catalog, dict) or not catalog:
                continue
            found=[]
            for name,urls in catalog.items():
                name = clean(name)
                if not name or not isinstance(urls, list):
                    continue
                for url in urls:
                    if not isinstance(url, str):
                        continue
                    parsed = urlsplit(url)
                    if parsed.scheme != 'https' or parsed.hostname != 'geil.to' or not parsed.path.startswith('/live/play/'):
                        continue
                    playback = 'plugin://' + VAVOO_ID + '/?' + urlencode({
                        'action':'play', 'url':url, 'name':name})
                    found.append({'name':name,'url':playback,'logo':'',
                                  'source':'Vavoo Plus','tvg_id':''})
                    if len(found)>=4500:
                        return found
            if found:
                return found
        except (OSError, TypeError, ValueError, KeyError) as exc:
            log('Vavoo-Cachedaten unlesbar: %s' % exc)
    return []


def _vavoo_from_directory():
    """Fallback for fresh installations: let Vavoo generate its own channel list.

    This calls the installed add-on instead of importing/copying its internal
    resolver. Kodi may show the source add-on's progress while first loading.
    """
    result = rpc('Files.GetDirectory', {'directory': VAVOO_ROUTE, 'media':'video',
                                        'properties':['title','art','thumbnail']})
    found=[]
    for item in (result.get('files') or []):
        url = str(item.get('file') or '')
        parts = urlsplit(url)
        if parts.scheme!='plugin' or parts.netloc!=VAVOO_ID:
            continue
        query = parse_qs(parts.query)
        if (query.get('action') or [''])[0] != 'play':
            continue
        name = clean((query.get('name') or [''])[0] or item.get('label') or item.get('title'))
        if not name:
            continue
        art = item.get('art') or {}
        logo = art.get('thumb') or art.get('icon') or item.get('thumbnail') or ''
        if isinstance(logo,str) and (logo.startswith('Default') or logo.endswith('fanart.jpg')):
            logo=''
        found.append({'name':name, 'url':url, 'logo':logo,
                      'source':'Vavoo Plus', 'tvg_id':(query.get('tvg_id') or [''])[0]})
        if len(found)>=4500:
            break
    return found


def _vavoo_channels():
    rows = _vavoo_from_cache()
    if not rows:
        rows = _vavoo_from_directory()
    if not rows:
        raise RuntimeError('Vavoo TV Plus hat keine deutschen Sender geliefert')
    return rows


def _attr(attrs, name):
    match = re.search(r'(?:^|\s)' + re.escape(name) + r'="([^"]*)"', attrs, flags=re.I)
    return match.group(1) if match else ''


def _identify(records):
    # Stable by visible channel name, with ordinal for multiple stream variants.
    counts = {}
    result = []
    for row in records:
        base = canonical(row['name']) or short_id(row['name'])
        key = row['source'] + ':' + base
        counts[key] = counts.get(key, 0) + 1
        row = dict(row)
        row['id'] = short_id('%s:%d' % (key, counts[key]))
        row['source_group'] = row.get('group','')
        row['group'] = group_for(row['name'])
        row['normalized'] = base
        result.append(row)
    return result


def _display_name(channels, key):
    """One label per station; retain individual provider variants separately."""
    familiar = {'daserste':'Das Erste', 'zdf':'ZDF', 'prosieben':'ProSieben',
                'kabeleins':'Kabel Eins', 'sat1':'SAT.1', 'rtl':'RTL',
                'rtlzwei':'RTLZWEI', 'vox':'VOX', '3sat':'3sat', 'arte':'arte',
                'zdfneo':'ZDFneo', 'zdfinfo':'ZDFinfo', 'welt':'WELT',
                'ntv':'ntv', 'kika':'KiKA', 'prosiebenmaxx':'ProSieben MAXX'}
    if key in familiar:
        return familiar[key]
    name=min((c.get('name','') for c in channels if c.get('name')), key=len, default=key)
    name=re.sub(r'(?:\s*[\(\[]\s*(?:stream\s*)?\d{1,3}\s*[\)\]])+\s*$', '', name)
    name=re.sub(r'\s+(?:HD|SD|FHD|UHD|4K|1080p|720p)(?:\s*\+)?\s*$', '', name, flags=re.I)
    return name.strip(' -|') or key


def _group_channels(rows):
    """Stable logical stations, while preserving all original stream URLs.

    Existing user data are left untouched. Only Vavoo TV Plus URLs are offered.
    """
    groups = {}
    for row in rows:
        key = canonical(row.get('name',''))
        if not key:
            continue
        groups.setdefault(key, []).append(row)
    preferred=load_json('stream-preferences.json', {})
    merged=[]
    for key, members in groups.items():
        aliases=set()
        variants=[]
        seen=set()
        for row in members:
            u=row.get('url','')
            if not u or u in seen:
                continue
            seen.add(u)
            aliases.add(row.get('id',''))
            variants.append({'id':row.get('id',''), 'name':row.get('name',''),
                             'url':u, 'source':row.get('source','')})
        if not variants:
            continue
        # A previously confirmed playable URL can be tried first.
        favorite_url=preferred.get(key,'')
        variants.sort(key=lambda v:(v['url']!=favorite_url,
                                    {'Vavoo Plus':0}.get(v['source'],3)))
        name=_display_name(members,key)
        first=members[0]
        merged.append({'id':'tv_'+short_id(key), 'name':name, 'normalized':key,
                       'url':variants[0]['url'], 'variants':variants,
                       'legacy_ids':sorted(aliases), 'logo':next((r.get('logo') for r in members if r.get('logo')), ''),
                       'tvg_id':next((r.get('tvg_id') for r in members if r.get('tvg_id')), '') or _vavoo_tvg_id(name),
                       'source':first.get('source','Vavoo Plus'), 'group':group_for(name),
                       'source_group':first.get('source_group','')})
    _migrate_user_data(merged)
    return merged


def _migrate_user_data(merged):
    """Carry existing favourites and manual EPG mappings to stable station IDs."""
    rawfav=load_json('favorites.json', [])
    fav=set(rawfav if isinstance(rawfav,list) else [])
    overrides=load_json('epg-overrides.json', {})
    if not isinstance(overrides,dict):
        overrides={}
    changed_fav=False
    changed_epg=False
    for row in merged:
        old=set(row['legacy_ids'])
        if fav.intersection(old):
            # Remove obsolete variant IDs: otherwise removing this new group
            # favourite would bring it back during the next cache read.
            if row['id'] not in fav:
                fav.add(row['id'])
            fav.difference_update(old)
            changed_fav=True
        if row['id'] not in overrides:
            for rid in old:
                if rid in overrides:
                    overrides[row['id']]=overrides[rid];changed_epg=True;break
    if changed_fav:
        save_json('favorites.json',sorted(fav))
    if changed_epg:
        save_json('epg-overrides.json',overrides)


def channel_list(force=False):
    """Use *only* Vavoo TV Plus for channel discovery and playback.

    On upgrade, old mixed-provider channels.json is never accepted as current.
    If Vavoo is briefly offline, keep only its previously cached rows, and
    always preserve favorites, overrides, logos, and EPG database untouched.
    """
    cache = load_json('channels.json', {})
    signature = 'vavoo-only-v1|' + _vavoo_signature()
    oldrows = [dict(c) for c in cache.get('channels', [])
               if isinstance(c, dict) and c.get('source') == 'Vavoo Plus'
               and urlsplit(str(c.get('url',''))).netloc == VAVOO_ID]
    if (not force and cache.get('signature') == signature
            and cache.get('expires', 0) > time.time()):
        rows = [c for c in cache.get('channels', [])
                if isinstance(c, dict) and c.get('source') == 'Vavoo Plus']
        if rows:
            return _group_channels(rows)
    errs=[]
    records=[]
    try:
        records=_vavoo_channels()
    except Exception as exc:
        errs=['Vavoo TV Plus: %s' % exc]
        log(errs[0])
    if not records:
        # Do not cache a missing provider over valid Vavoo channels.
        return _group_channels(oldrows)
    rows=_identify(records)
    old_map={c.get('id'):c.get('url') for c in oldrows}
    new_map={c.get('id'):c.get('url') for c in rows}
    report={'added':len(new_map.keys()-old_map.keys()),
            'removed':len(old_map.keys()-new_map.keys()),
            'updated':sum(1 for k in new_map.keys()&old_map.keys()
                          if new_map[k]!=old_map[k]),
            'total':len(rows),'at':time.time(),'errors':errs,
            'source':'Vavoo TV Plus'}
    save_json('channels.json', {'channels':rows,'signature':signature,
             'expires':time.time()+max(1,int(settings().get('channel_refresh_hours',6)))*3600,
             'report':report})
    return _group_channels(rows)


def favorites():
    return set(load_json('favorites.json', []))


def set_favorite(channel_id, value):
    ids = favorites()
    ids.add(channel_id) if value else ids.discard(channel_id)
    save_json('favorites.json', sorted(ids))


RECENT_LIMIT = 25


def recent_history():
    """Small, private record of *successfully started* TV channels only."""
    records = load_json('recent-tv.json', [])
    if not isinstance(records, list):
        return []
    return [r for r in records[:RECENT_LIMIT] if isinstance(r, dict)
            and isinstance(r.get('id'), str) and r.get('id')]


def remember_station(ch):
    """Call ONLY after playback has been verified; no unplayed attempts."""
    row = {'id': ch['id'], 'name': ch['name'],
           'normalized': ch.get('normalized', ''), 'last_seen': int(time.time())}
    kept = [r for r in recent_history() if r['id'] != ch['id']
            and not (row['normalized'] and r.get('normalized') == row['normalized'])]
    save_json('recent-tv.json', ([row] + kept)[:RECENT_LIMIT])


def recent_channels(query=''):
    """Resolve live provider variants afresh; never persist stale stream URLs."""
    records = recent_history()
    if not records:
        return []
    query_key = canonical(query) if query else ''
    requested = [r for r in records if not query_key or
                 query_key in canonical(r.get('name', ''))]
    if not requested:
        return []
    # Batch resolve once: up to 25 recent entries must NOT re-read every Vavoo
    # playlist 25 times, which caused long delays on slower TV sticks.
    snapshot = load_json('channels.json', {}).get('channels', [])
    snapshot = [x for x in snapshot if isinstance(x, dict)
                and x.get('source') == 'Vavoo Plus']
    available = _group_channels(snapshot) if snapshot else []
    lookup = {ch['id']: ch for ch in available}
    missing = {r['id'] for r in requested if r['id'] not in lookup}
    for slug, _ in group_catalog():
        if not missing:
            break
        for ch in _vavoo_group_channels(slug, available):
            if ch['id'] in missing:
                lookup[ch['id']] = ch
                missing.discard(ch['id'])
    return [lookup[r['id']] for r in requested if r['id'] in lookup]


def list_group(group='all', query=''):
    if group == 'recent':
        return recent_channels(query)
    if group == 'favorites':
        wanted = favorites()
        if not wanted:
            return []
        raw = load_json('channels.json', {}).get('channels', [])
        available = _group_channels([row for row in raw if row.get('source') == 'Vavoo Plus'])
        found = {row['id']: row for row in available if row['id'] in wanted}
        for slug, _ in group_catalog():
            if wanted.issubset(found):
                break
            for row in _vavoo_group_channels(slug, available):
                if row['id'] in wanted:
                    found[row['id']] = row
        q = canonical(query) if query else ''
        return sorted((row for row in found.values() if not q or q in canonical(row['name'])),
                      key=lambda row: canonical(row['name']))
    # Vavoo's real curated group playlists must remain usable even when the
    # large country catalogue / its remote server is temporarily unavailable.
    if group.startswith('vavoo_'):
        snapshot=load_json('channels.json',{}).get('channels',[])
        snapshot=[x for x in snapshot if isinstance(x,dict) and x.get('source')=='Vavoo Plus']
        channels=_vavoo_group_channels(group,_group_channels(snapshot))
    else:
        channels = channel_list()
    fav = favorites()
    if group == 'favorites':
        channels = [c for c in channels if c['id'] in fav]
    elif group != 'all' and not group.startswith('vavoo_'):
        channels = [c for c in channels if c.get('group') == group]
    if query:
        q = canonical(query)
        channels = [c for c in channels if q in c['normalized']]
    return sorted(channels, key=lambda c: (canonical(c['name']), c['name']))


def find_channel(channel_id):
    # First use existing provider snapshot; avoid a slow network refresh
    # when a user selects a channel from an already loaded Vavoo group.
    snapshot=load_json('channels.json',{}).get('channels',[])
    raw=[x for x in snapshot if isinstance(x,dict) and x.get('source')=='Vavoo Plus']
    allchannels=_group_channels(raw) if raw else []
    station=next((c for c in allchannels if c['id'] == channel_id), None)
    if station:
        return station
    # Vavoo's supplied favourite-group playlists contain stations that are
    # absent from the country catalogue. These still need to play correctly.
    for slug,_title in group_catalog():
        if slug.startswith('vavoo_'):
            station=next((c for c in _vavoo_group_channels(slug,allchannels)
                          if c['id']==channel_id),None)
            if station:
                return station
    return None
