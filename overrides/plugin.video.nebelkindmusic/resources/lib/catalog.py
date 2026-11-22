"""Public YouTube playlist discovery with a durable, local-first catalogue.

Only ordinary playlists are accepted: personalised RD mixes are not supported
by the installed YouTube add-on's playlistItems route. No API keys are used.
"""
import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PLAYLIST_ID = re.compile(r'^(?:PL[A-Za-z0-9_-]{10,80}|OLAK5uy_[A-Za-z0-9_-]{10,80})$')
TTL = 24 * 60 * 60
MAX_RESPONSE = 5_000_000


def initial_data(html):
    marker = re.search(r'(?:var\s+ytInitialData|window\["ytInitialData"\])\s*=\s*', html)
    if not marker:
        raise ValueError('YouTube lieferte keine Playlist-Daten')
    return json.JSONDecoder().raw_decode(html[marker.end():])[0]


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def text(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ''
    return value.get('simpleText') or value.get('content') or ''.join(
        r.get('text', '') for r in value.get('runs', []))


def search_results(data, limit=30):
    results = []
    seen = set()
    for node in walk(data):
        old = node.get('playlistRenderer')
        new = node.get('lockupViewModel')
        if old:
            pid = old.get('playlistId', '')
            title = text(old.get('title'))
            owner = text(old.get('shortBylineText'))
        elif new and 'PLAYLIST' in new.get('contentType', ''):
            pid = new.get('contentId', '')
            meta = new.get('metadata', {}).get('lockupMetadataViewModel', {})
            title = text(meta.get('title'))
            rows = meta.get('metadata', {}).get('contentMetadataViewModel', {}).get('metadataRows', [])
            parts = rows[0].get('metadataParts', []) if rows else []
            owner = text(parts[0].get('text')) if parts else ''
        else:
            continue
        if not PLAYLIST_ID.fullmatch(pid) or not title or pid in seen:
            continue
        seen.add(pid)
        results.append(dict(name=title[:180], playlist_id=pid, owner=owner[:120]))
        if len(results) >= limit:
            break
    return results


def fetch(url):
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0',
                                   'Accept-Language': 'de-DE,de;q=0.9,en;q=0.8'})
    with urlopen(request, timeout=8) as response:
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError('YouTube-Antwort zu groß')
    return initial_data(raw.decode('utf-8'))


def discover(query, limit=30):
    return search_results(fetch('https://www.youtube.com/results?' + urlencode(
        {'search_query': query, 'sp': 'EgIQAw==', 'hl': 'de', 'gl': 'DE'})), limit)


def inspect_playlist(pid):
    if not PLAYLIST_ID.fullmatch(str(pid)):
        return False
    data = fetch('https://www.youtube.com/playlist?' + urlencode({'list': pid}))
    return playlist_has_entries(data)


def playlist_has_entries(data):
    # YouTube now uses lockupViewModel for playlist tracks as well as search.
    # Check only the actual tab contents, never unrelated menus/recommendations.
    for node in walk(data.get('contents', {})):
        if node.get('playlistVideoRenderer', {}).get('isPlayable') is True:
            return True
        video = node.get('lockupViewModel', {})
        if (video.get('contentType') == 'LOCKUP_CONTENT_TYPE_VIDEO'
                and re.fullmatch(r'[A-Za-z0-9_-]{11}', video.get('contentId', ''))
                and any(n.get('watchEndpoint', {}).get('videoId') == video['contentId']
                        for n in walk(video))):
            return True
    return False


def playlist_tracks(data):
    """Read available video IDs from the public playlist tab, not Data API."""
    tracks=[];seen=set()
    for node in walk(data.get('contents', {})):
        old=node.get('playlistVideoRenderer')
        new=node.get('lockupViewModel')
        if old and old.get('isPlayable') is True:
            video_id=old.get('videoId','');title=text(old.get('title'))
        elif new and new.get('contentType')=='LOCKUP_CONTENT_TYPE_VIDEO':
            video_id=new.get('contentId','')
            meta=new.get('metadata',{}).get('lockupMetadataViewModel',{})
            title=text(meta.get('title'))
            if not any(n.get('watchEndpoint',{}).get('videoId')==video_id for n in walk(new)):continue
        else:continue
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id) or video_id in seen or not title:continue
        seen.add(video_id);tracks.append(dict(video_id=video_id,title=title[:240]))
        if len(tracks)>=100:break
    return tracks


def load_tracks(pid,cache_dir):
    if not PLAYLIST_ID.fullmatch(str(pid or '')):raise ValueError('Ungültige Playlist')
    path=Path(cache_dir)/(pid+'.json')
    saved=read(path,{})
    if saved.get('tracks') and time.time()-saved.get('at',0)<3600:return saved
    try:
        data=fetch('https://www.youtube.com/playlist?'+urlencode({'list':pid}))
        tracks=playlist_tracks(data)
        if not tracks:raise ValueError('Playlist ist leer, privat oder nicht verfügbar')
        more=len(tracks)>=100 or any('continuationItemRenderer' in n or 'continuationItemViewModel' in n for n in walk(data.get('contents',{})))
        result=dict(tracks=tracks,more=more,at=time.time())
        save(path,result)
        return result
    except (OSError,ValueError):
        if saved.get('tracks'):return dict(saved,stale=True)
        raise


def read(path, fallback=None):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else fallback
    except (OSError, ValueError):
        return fallback


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def load_catalog(seed_path, cache_path):
    seed = read(seed_path, {'categories': {}})
    cached = read(cache_path, {})
    # Category structure/art comes only from the packaged catalogue.
    for name, info in seed.get('categories', {}).items():
        recent = cached.get('categories', {}).get(name, {}).get('playlists', [])
        if recent:
            unique = {}
            for playlist in recent + info.get('playlists', []):
                pid = playlist.get('playlist_id', '')
                if PLAYLIST_ID.fullmatch(pid) and playlist.get('name'):
                    unique.setdefault(pid, playlist)
            info['playlists'] = list(unique.values())[:60]
    return seed


def refresh(seed_path, cache_path, force=False, abort=lambda: False):
    seed = read(seed_path, {'categories': {}})
    cached = read(cache_path, {'categories': {}})
    if not force and time.time() - cached.get('checked_at', 0) < TTL:
        return 0
    # Sequential requests keep CPU/memory and network pressure low on Fire TV.
    # Each successful category is saved before moving on; failures retain data.
    updated = 0
    for name, info in seed.get('categories', {}).items():
        if abort():
            return updated
        queries = info.get('queries', [])
        if not queries:
            continue
        try:
            playlists = discover(queries[0])
            if not playlists:
                continue
            cached.setdefault('categories', {})[name] = dict(playlists=playlists, updated_at=time.time())
            save(cache_path, cached)
            updated += 1
        except (OSError, ValueError, KeyError):
            continue
    # A failed refresh gets a short retry delay, not another fetch on each view.
    cached['checked_at'] = time.time() if updated else time.time() - TTL + 3600
    save(cache_path, cached)
    return updated
