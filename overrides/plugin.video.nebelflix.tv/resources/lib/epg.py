"""Multi-provider German XMLTV: transactional refresh, exact mapping, fallback.

Programmes are never scraped from a stream; absence of programme data remains
explicit. A failed feed refresh does not replace the last valid data.
"""
import gzip
import io
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from .common import DATA, canonical, clean, download, settings, log, load_json, save_json, epg_catalog, vavoo_epg_url

DB = DATA / 'guide.sqlite'
MAX_FUTURE = 3 * 86400
MAX_RAW = 30 * 1024 * 1024
MAX_UNPACKED = 165 * 1024 * 1024


class BoundedReader:
    def __init__(self, stream, maximum):
        self.stream, self.maximum, self.count = stream, maximum, 0
    def read(self, n=-1):
        value = self.stream.read(65536 if n is None or n < 0 else min(n,65536))
        self.count += len(value)
        if self.count > self.maximum:
            raise ValueError('XMLTV ist zu gross')
        return value


def connect():
    c = sqlite3.connect(str(DB), timeout=15)
    c.execute('PRAGMA busy_timeout=15000')
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS guides (source TEXT, cid TEXT, name TEXT, logo TEXT, PRIMARY KEY(source,cid))')
    c.execute('CREATE TABLE IF NOT EXISTS shows (source TEXT, cid TEXT, starts INTEGER, ends INTEGER, title TEXT, description TEXT)')
    c.execute('CREATE INDEX IF NOT EXISTS shows_lookup ON shows(source,cid,starts,ends)')
    c.execute('CREATE TABLE IF NOT EXISTS state (source TEXT PRIMARY KEY, last_success INTEGER, count INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS programme_art (source TEXT, cid TEXT, starts INTEGER, url TEXT, PRIMARY KEY(source,cid,starts))')
    c.commit()
    return c


def timestamp(raw):
    try:
        text = str(raw or '').strip()
        stamp = text[:14]
        if len(stamp) == 12:
            stamp += '00'
        date = datetime.strptime(stamp, '%Y%m%d%H%M%S')
        match = re.search(r'([+-])(\d{2})(\d{2})$', text)
        if match:
            delta = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
            if match.group(1) == '-':
                delta = -delta
            tz = timezone(delta)
        else:
            tz = timezone.utc
        return int(date.replace(tzinfo=tz).timestamp())
    except (ValueError, OverflowError):
        return 0


def available():
    conf = settings()
    enabled = set(conf.get('sources', []))
    return [(k, name, url) for k,name,url in epg_catalog() if k in enabled]


def source_status():
    with connect() as conn:
        data = {r[0]:{'last_success':r[1], 'shows':r[2]} for r in conn.execute('SELECT source,last_success,count FROM state')}
    return data


def _channel_candidates(cid, names, normalized_targets):
    matched = {canonical(v) for v in ([cid.split('@')[0].rsplit('.',1)[0]] + names)}
    return bool(matched & normalized_targets)


def refresh(source, force=False, target_channels=None):
    """Stream one feed into a DB transaction; preserve old data on any failure."""
    item = next((x for x in epg_catalog() if x[0] == source), None)
    if item is None:
        raise ValueError('Unbekannte EPG-Quelle')
    c = connect()
    try:
        last = c.execute('SELECT last_success FROM state WHERE source=?', (source,)).fetchone()
        configured_urls = load_json('epg-source-urls.json', {})
        changed_url = (source == 'vavoo' and configured_urls.get('vavoo') != item[2])
        if not force and not changed_url and last and time.time() - last[0] < 3600 * max(1,int(settings().get('epg_refresh_hours',6))):
            return 0
        if target_channels is None:
            from .channels import channel_list
            target_channels = channel_list()
        wanted = {canonical(ch.get('name','')) for ch in target_channels}
        if not wanted:
            return 0
        raw = download(item[2], MAX_RAW, timeout=20)
        reader = gzip.GzipFile(fileobj=io.BytesIO(raw)) if raw[:2] == b'\x1f\x8b' else io.BytesIO(raw)
        stream = BoundedReader(reader, MAX_UNPACKED)
        now = int(time.time())
        matched_channels = {}
        channel_rows, program_rows, art_rows, count = [], [], [], 0
        root = None
        # Begin only after successful network transfer. Rollback means no data loss.
        c.execute('BEGIN')
        c.execute('DELETE FROM guides WHERE source=?', (source,))
        c.execute('DELETE FROM shows WHERE source=?', (source,))
        c.execute('DELETE FROM programme_art WHERE source=?', (source,))
        for event, elem in ET.iterparse(stream, events=('start','end')):
            if root is None:
                root = elem
            if event != 'end':
                continue
            tag = elem.tag.rsplit('}',1)[-1]
            if tag == 'channel':
                cid = str(elem.get('id') or '')
                names = [clean(v.text) for v in elem.findall('display-name') if v.text]
                if cid and _channel_candidates(cid, names, wanted):
                    logo_tag = elem.find('icon')
                    logo = logo_tag.get('src', '') if logo_tag is not None else ''
                    channel_rows.append((source,cid,'|'.join(names) if names else cid,logo))
                    matched_channels[cid] = 1
            elif tag == 'programme':
                cid = str(elem.get('channel') or '')
                if cid in matched_channels:
                    start, end = timestamp(elem.get('start')), timestamp(elem.get('stop'))
                    if end > now - 3600 and start < now + MAX_FUTURE and 0 < start < end:
                        title_tag, desc_tag = elem.find('title'), elem.find('desc')
                        title = clean(title_tag.text if title_tag is not None else '')
                        desc = clean(desc_tag.text if desc_tag is not None else '')[:600]
                        if title:
                            program_rows.append((source,cid,start,end,title,desc))
                            image = elem.find('icon')
                            art_url = str(image.get('src','')) if image is not None else ''
                            if art_url.startswith('https://') and len(art_url) < 2048:
                                art_rows.append((source,cid,start,art_url))
                                if len(art_rows) >= 250:
                                    c.executemany('INSERT OR REPLACE INTO programme_art VALUES (?,?,?,?)', art_rows)
                                    art_rows = []
                            count += 1
                            if len(program_rows) >= 1000:
                                c.executemany('INSERT INTO shows VALUES (?,?,?,?,?,?)', program_rows)
                                program_rows = []
            if tag in ('programme', 'channel'):
                elem.clear()
                if root is not None:
                    root.clear()
        if program_rows:
            c.executemany('INSERT INTO shows VALUES (?,?,?,?,?,?)',program_rows)
        if art_rows:
            c.executemany('INSERT OR REPLACE INTO programme_art VALUES (?,?,?,?)', art_rows)
        if channel_rows:
            c.executemany('INSERT INTO guides VALUES (?,?,?,?)',channel_rows)
        if count < 1 or len(channel_rows) < 1:
            raise ValueError('EPG enthält keine aktuellen, zuordenbaren Sendungen')
        c.execute('INSERT OR REPLACE INTO state(source,last_success,count) VALUES (?,?,?)',
                  (source,now,count))
        c.commit()
        if source == 'vavoo':
            configured_urls['vavoo'] = item[2]
            save_json('epg-source-urls.json', configured_urls)
        log('%s: %d passende EPG-Sender, %d Sendungen' % (source,len(channel_rows),count))
        return count
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def refresh_due(limit=1, force=False, target_channels=None):
    stats, errors = [], []
    if not force:
        try:
            import_vavoo_epg_cache(target_channels)
        except Exception as exc:
            log('Vavoo EPG-Cachereuse: %s' % exc)
    current = source_status()
    last_attempt=load_json('epg-attempts.json',{})
    for source,title,url in available():
        age = time.time() - current.get(source,{}).get('last_success',0)
        changed_url = (source == 'vavoo' and
                       load_json('epg-source-urls.json', {}).get('vavoo') != url)
        # Failed feeds back off for at least 30 min, without losing old data.
        if not force and not changed_url and time.time() - last_attempt.get(source,0) < 1800:
            continue
        if not force and not changed_url and age < max(1,int(settings().get('epg_refresh_hours',6)))*3600:
            continue
        try:
            last_attempt[source]=time.time()
            save_json('epg-attempts.json',last_attempt)
            stats.append((source,refresh(source,force=force,target_channels=target_channels)))
        except Exception as exc:
            errors.append('%s: %s' % (title,exc))
            log('EPG %s failed: %s' % (source,exc))
        if limit and (len(stats)+len(errors)) >= limit:
            break
    return stats,errors


def import_vavoo_epg_cache(target_channels=None):
    """Reuse Vavoo's valid, already parsed EPG without downloading it again.

    Read-only input: addon_data/plugin.video.vavoo.tv.plus/epg_cache.json.
    Only a matching, unexpired URL and programmes for known stations are used.
    A failed or partial import leaves every Nebelflix EPG source intact.
    """
    from pathlib import Path
    from datetime import datetime, timezone
    import json
    import xbmcaddon, xbmcvfs

    configured = vavoo_epg_url()
    if not configured:
        return 0
    dest_source = next((key for key,_,url in epg_catalog() if url == configured), None)
    if dest_source is None or dest_source not in settings().get('sources',[]):
        return 0
    try:
        profile = Path(xbmcvfs.translatePath(
            xbmcaddon.Addon('plugin.video.vavoo.tv.plus').getAddonInfo('profile')))
        cache_file = profile / 'epg_cache.json'
        if not cache_file.is_file() or cache_file.stat().st_size > 48 * 1024 * 1024:
            return 0
        mtime = int(cache_file.stat().st_mtime)
        # Avoid parsing a potentially large JSON file on every EPG poll.
        with connect() as conn:
            previous = conn.execute('SELECT last_success FROM state WHERE source=?',
                                    (dest_source,)).fetchone()
            # A native guide for a shared source already exists: do not
            # overwrite it with a potentially narrower external cache.
            if previous and dest_source != 'vavoo':
                return 0
            if previous and previous[0] >= mtime:
                return 0
        with cache_file.open('r', encoding='utf-8') as f:
            cached = json.load(f)
        if (not isinstance(cached, dict) or cached.get('url') != configured or
                float(cached.get('expires') or 0) < time.time() or
                not isinstance(cached.get('data'), dict)):
            return 0
    except (OSError, ValueError, TypeError) as exc:
        log('Vavoo EPG Diskcache nicht verwendbar: %s' % exc)
        return 0

    if target_channels is None:
        from .channels import channel_list
        target_channels = channel_list()
    wanted = {canonical(ch.get('name','')) for ch in target_channels}
    wanted_ids = {str(ch.get('tvg_id','')).lower() for ch in target_channels if ch.get('tvg_id')}
    if not wanted:
        return 0
    now = int(time.time())
    guide_rows, show_rows, count = [], [], 0
    for cid, events in list(cached['data'].items())[:3500]:
        if not isinstance(cid, str) or not isinstance(events, list):
            continue
        candidate = canonical(cid.rsplit('.',1)[0])
        if candidate not in wanted and cid.lower() not in wanted_ids:
            continue
        rows=[]
        for event in events[:200]:
            if not isinstance(event, (list,tuple)) or len(event) < 4:
                continue
            try:
                first = datetime.fromisoformat(event[0].replace('Z','+00:00'))
                last = datetime.fromisoformat(event[1].replace('Z','+00:00'))
                if first.tzinfo is None: first = first.replace(tzinfo=timezone.utc)
                if last.tzinfo is None: last = last.replace(tzinfo=timezone.utc)
                starts,ends = int(first.timestamp()),int(last.timestamp())
                title, desc = clean(event[2]),clean(event[3])[:600]
                if title and ends > now-3600 and starts < now+MAX_FUTURE and starts < ends:
                    rows.append((dest_source,cid,starts,ends,title,desc))
            except (ValueError, TypeError, OverflowError, AttributeError):
                continue
        if rows:
            guide_rows.append((dest_source,cid,cid,''))
            show_rows.extend(rows)
            count += len(rows)
    if not guide_rows or not show_rows:
        return 0
    with connect() as conn:
        try:
            conn.execute('BEGIN')
            conn.execute('DELETE FROM guides WHERE source=?',(dest_source,))
            conn.execute('DELETE FROM shows WHERE source=?',(dest_source,))
            conn.execute('DELETE FROM programme_art WHERE source=?',(dest_source,))
            conn.executemany('INSERT INTO guides VALUES (?,?,?,?)',guide_rows)
            conn.executemany('INSERT INTO shows VALUES (?,?,?,?,?,?)',show_rows)
            conn.execute('INSERT OR REPLACE INTO state VALUES (?,?,?)',
                         (dest_source,int(cache_file.stat().st_mtime),count))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if dest_source == 'vavoo':
        urls=load_json('epg-source-urls.json',{})
        urls['vavoo']=configured
        save_json('epg-source-urls.json', urls)
    log('Vavoo EPG-Diskcache übernommen: %d Sender, %d Sendungen' %
        (len(guide_rows), count))
    return count


def _matches(ch, indexed):
    """Index once, then O(1) source matching for thousands of channel tiles."""
    cid = ch.get('id', '')
    cache = indexed.setdefault('_match_cache', {})
    if cid in cache:
        return cache[cid]
    target = canonical(ch.get('name',''))
    tvg = str(ch.get('tvg_id') or '').lower()
    matched = {}
    override = indexed.get('_overrides', {}).get(cid, {})
    for source,_,_ in available():
        named = indexed.get('_by_name', {}).get(source, {})
        ids = indexed.get('_by_id', {}).get(source, {})
        if override.get('source') == source:
            chosen = ids.get(str(override.get('cid','')).lower(), [])
            if len(chosen) == 1:
                matched[source] = chosen[0]
                continue
        if tvg:
            exact = ids.get(tvg, [])
            if len(exact) == 1:
                matched[source] = exact[0]
                continue
        candidates = named.get(target, [])
        if len(candidates) == 1:
            matched[source] = candidates[0]
    cache[cid] = matched
    return matched


def guide_index(conn):
    indexed = {'_overrides':load_json('epg-overrides.json',{}),
               '_by_name':{}, '_by_id':{}, '_match_cache':{}, '_available': available()}
    for source,cid,name,logo in conn.execute('SELECT source,cid,name,logo FROM guides'):
        entry = (cid,name,logo)
        indexed.setdefault(source, []).append(entry)
        ids = indexed['_by_id'].setdefault(source,{})
        ids.setdefault(cid.lower(), []).append(entry)
        named = indexed['_by_name'].setdefault(source,{})
        for key in {canonical(v) for v in name.split('|')} | {canonical(cid.rsplit('.',1)[0])}:
            if key:
                named.setdefault(key, []).append(entry)
    return indexed


def match_channel(ch, index=None, conn=None):
    if index is None:
        if conn is None:
            c=connect()
            try:
                return match_channel(ch, guide_index(c))
            finally:
                c.close()
        index = guide_index(conn)
    return _matches(ch,index)



# Vavoo TV Plus already parses XMLTV into this independent disk cache. Reuse
# the currently broadcast programme read-only when our merged guide does not
# know it. Keep the cache isolated from Nebelflix's guide.sqlite.
_VAVOO_LIVE_MEM={'stamp':None,'data':None,'lookup':None,'by_id':None,'expires':0,'checked':0}


def _vavoo_live_cache():
    from pathlib import Path
    import json
    import xbmcaddon, xbmcvfs
    mem=_VAVOO_LIVE_MEM
    try:
        base=Path(xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.vavoo.tv.plus').getAddonInfo('profile')))
        path=base/'epg_cache.json'
        stat=path.stat()
        if stat.st_size > 55*1024*1024 or stat.st_size < 30:
            return None, None, None
        stamp=(str(path),stat.st_mtime_ns,stat.st_size)
        if mem['stamp']!=stamp:
            data=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data,dict) or not isinstance(data.get('data'),dict):
                return None,None,None
            mem['data']=data['data']
            mem['rows']={}
            # Names can be duplicated or ambiguous. Only unique exact
            # normalized identities are matched; no substring matching.
            names={}
            for cid in mem['data']:
                for key in (canonical(cid),canonical(cid.rsplit('.',1)[0])):
                    if key:
                        names.setdefault(key,[]).append(cid)
            mem['lookup']={k:list(dict.fromkeys(v)) for k,v in names.items()}
            mem['by_id']={k.casefold():k for k in mem['data']}
            mem['stamp']=stamp
            mem['expires']=float(data.get('expires') or 0)
        # Do not discard broadcasts covering the current time merely because
        # the provider's refresh TTL expired. Programme times are checked below.
        return mem['data'],mem['lookup'],mem['by_id']
    except (OSError,TypeError,ValueError,OverflowError):
        return None,None,None


def _vavoo_live_programme(ch, now):
    """Vavoo cached XMLTV entries: tuples identical to Nebelflix guide rows."""
    from datetime import datetime,timezone
    data,names,id_lookup=_vavoo_live_cache()
    if not data:
        return None
    try:
        from .channels import _vavoo_tvg_id
        ids=[ch.get('tvg_id',''),_vavoo_tvg_id(ch.get('name','')),ch.get('name','')]
        for var in ch.get('variants',[]):
            ids.append(var.get('name',''))
        cid=None
        # Literal IDs (especially numeric tvg-ids) are stronger than names.
        for ident in ids:
            if ident and ident.casefold() in id_lookup:
                cid=id_lookup[ident.casefold()];break
        if cid is None:
            for ident in ids:
                key=canonical(ident)
                found=names.get(key,[]) if key else []
                if len(found)==1:
                    cid=found[0];break
        if cid is None:
            return None
        nowrow=None;future=None
        parsed = _VAVOO_LIVE_MEM.setdefault('rows', {})
        if cid not in parsed:
            rows = []
            for entry in data[cid]:
                if not isinstance(entry,(tuple,list)) or len(entry)<4:
                    continue
                try:
                    first=datetime.fromisoformat(str(entry[0]).replace('Z','+00:00'))
                    last=datetime.fromisoformat(str(entry[1]).replace('Z','+00:00'))
                    if first.tzinfo is None:first=first.replace(tzinfo=timezone.utc)
                    if last.tzinfo is None:last=last.replace(tzinfo=timezone.utc)
                    a,b=int(first.timestamp()),int(last.timestamp())
                except (TypeError,ValueError,OverflowError):
                    continue
                title=clean(entry[2])
                if not title or not a<b:continue
                rows.append((a,b,title,clean(entry[3])[:600]))
            parsed[cid] = rows
        for row in parsed[cid]:
            a, b = row[:2]
            if a<=now<b and (nowrow is None or a>nowrow[0]):nowrow=row
            if a>now and (future is None or a<future[0]):future=row
        if nowrow or future:
            return {'source':'vavoo-cache','id':cid,'logo':'',
                    'now':nowrow,'next':future,'programme_art':''}
    except Exception as exc:
        log('Vavoo-EPG-Liveanzeige: %s'%exc)
    return None


def programme(ch, moment=None, conn=None, index=None):
    """Merge providers by broadcast status, not just provider order.

    Earlier versions returned as soon as the first feed had *any upcoming*
    programme, even if the next feed contained the programme currently on air.
    The selected current show is always real XMLTV data (never extrapolated).
    """
    now = int(moment if moment is not None else time.time())
    if conn is None:
        with connect() as c:
            return programme(ch, now, c, guide_index(c))
    hits = match_channel(ch,index,conn)
    current = None
    next_candidates = []
    fallback = None
    for source,_,_ in (index.get('_available', []) if index is not None and '_available' in index else available()):
        hit = hits.get(source)
        if not hit:
            continue
        cid = hit[0]
        active = conn.execute('SELECT starts,ends,title,description FROM shows '
              'WHERE source=? AND cid=? AND starts<=? AND ends>? ORDER BY starts DESC LIMIT 1',
              (source,cid,now,now)).fetchone()
        upcoming = conn.execute('SELECT starts,ends,title,description FROM shows '
              'WHERE source=? AND cid=? AND starts>? ORDER BY starts LIMIT 1',
              (source,cid,now)).fetchone()
        candidate=(source,cid,hit[2])
        if fallback is None:
            fallback=candidate
        if upcoming:
            next_candidates.append((upcoming[0],candidate,upcoming))
        if active and current is None:
            current=(candidate,active)
    if current:
        meta, active = current
        # First choose the next programme from the same provider, when it has
        # one; otherwise pick the nearest upcoming entry from another feed.
        candidates=[(start,candidate,show) for start,candidate,show in next_candidates
                    if start >= active[1]-60]
        preferred=next(((candidate,show) for _,candidate,show in candidates
                        if candidate[0] == meta[0]), None)
        follow=preferred or ((min(candidates,key=lambda t:t[0])[1:]) if candidates else None)
        image=conn.execute('SELECT url FROM programme_art WHERE source=? AND cid=? AND starts=?',
                           (meta[0],meta[1],active[0])).fetchone()
        return {'source':meta[0],'id':meta[1],'logo':meta[2],
                'now':active,'next':follow[1] if follow else None,
                'programme_art':image[0] if image else ''}
    # The Vavoo addon can already show a programme even if our SQL guide
    # has not imported/matched its own EPG cache. Prefer its active broadcast
    # to another provider's merely upcoming listing.
    vavoo=_vavoo_live_programme(ch,now)
    if vavoo and vavoo.get('now'):
        return vavoo
    if next_candidates:
        _,meta,upcoming=min(next_candidates,key=lambda t:t[0])
        return {'source':meta[0],'id':meta[1],'logo':meta[2],
                'now':None,'next':upcoming,'programme_art':''}
    if vavoo and vavoo.get('next'):
        return vavoo
    return {'source':fallback[0] if fallback else '',
            'id':fallback[1] if fallback else '',
            'logo':fallback[2] if fallback else '',
            'now':None,'next':None,'programme_art':''}


def at_time(ch, moment, conn=None, index=None):
    if conn is None:
        with connect() as c:
            return at_time(ch,moment,c,guide_index(c))
    hits = match_channel(ch,index,conn)
    for source,_,_ in (index.get('_available', []) if index is not None and '_available' in index else available()):
        if source not in hits:
            continue
        cid = hits[source][0]
        active=conn.execute('SELECT starts,ends,title,description FROM shows WHERE '
                'source=? AND cid=? AND starts<=? AND ends>? ORDER BY starts DESC LIMIT 1',
                (source,cid,moment,moment)).fetchone()
        if active:
            return active
    # The Vavoo Plus addon may show EPG before our independent XMLTV import
    # has filled guide.sqlite. Use its existing read-only disk cache for the
    # requested point in time, not just for the current OSD programme.
    cached = _vavoo_live_programme(ch, int(moment))
    if cached:
        return cached.get('now')
    return None


def schedule(ch, hours=12):
    now = int(time.time())
    with connect() as conn:
        hits = match_channel(ch, guide_index(conn))
        for source,_,_ in available():
            if source not in hits:
                continue
            cid = hits[source][0]
            rows = list(conn.execute('SELECT starts,ends,title,description FROM shows WHERE '
                   'source=? AND cid=? AND ends>? AND starts<? ORDER BY starts LIMIT 80',
                   (source,cid,now,now+hours*3600)))
            if rows:
                return source,rows
    return '',[]


def coverage(channels):
    with connect() as conn:
        index = guide_index(conn)
        paired = []
        missing = []
        for ch in channels:
            m = match_channel(ch,index,conn)
            if m:
                paired.append(ch)
            else:
                missing.append(ch.get('name',''))
        return {'total':len(channels),'matched':len(paired),'missing':missing}
