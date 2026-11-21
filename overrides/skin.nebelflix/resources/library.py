"""Profile-local layout, independent widget loading and playback progress."""
import json
import queue
import random
import re
import threading
import time
import uuid
from bounded_workers import SearchExecutor as ThreadPoolExecutor
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import xbmc
import xbmcgui
import xbmcvfs
import vavoo_live
from artwork import Artwork
import nebelflix_tv
import history_access
import performance
import load_timing
import fast_catalog
import hub_protocol

ROOT = Path(xbmcvfs.translatePath('special://profile/addon_data/skin.nebelflix/'))
SKIN = Path(__file__).resolve().parents[1]
MAX_TABS = 12
MAX_ROWS = 48
PROVIDER_CACHE = {}
PROVIDER_CACHE_LOCK = threading.Lock()
PROVIDER_MATCH = {
    'netflix': ('netflix',),
    'prime': ('amazon prime video', 'prime video', 'amazon video'),
    'disney': ('disney+', 'disney plus'),
    'rtl': ('rtl+', 'rtl plus'),
    'wow': ('wow', 'sky wow'),
    'paramount': ('paramount+', 'paramount plus'),
}


def widget_cache_ttl(path):
    """Discovery TTL shared with the lightweight cached-list provider."""
    return hub_protocol.widget_ttl(path)


def read(name, default):
    # Windows can briefly deny reads during replacement/copy. Never let a
    # transient sharing violation crash a Kodi directory provider.
    for attempt in range(3):
        try:
            return json.loads((ROOT / name).read_text(encoding='utf-8'))
        except FileNotFoundError:
            break
        except (OSError, json.JSONDecodeError):
            if attempt < 2:
                time.sleep(0.02)
    try:
        return json.loads((ROOT / (name + '.bak')).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return default


def write(name, data):
    ROOT.mkdir(parents=True, exist_ok=True)
    target = ROOT / name
    temp = ROOT / (name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    if target.exists():
        shutil.copyfile(target, ROOT / (name + '.bak'))
    try:
        temp.replace(target)
    except PermissionError:
        # Some Windows Kodi installations deny replace/delete on existing files.
        # Keep a valid backup and copy the fully written temporary file instead.
        shutil.copyfile(temp, target)
        temp.unlink(missing_ok=True)



# Reset is deliberately deferred to the next controller startup. A live Widgets
# worker may otherwise recreate the cache immediately after an in-place delete.
WIDGET_RESET_MARKER = 'widget-cache-reset-request.json'


def schedule_widget_cache_reset(include_continue=False):
    """Request a reset for next start; playback progress is never deleted."""
    write(WIDGET_RESET_MARKER, {'requested_at': time.time(),
                                'hide_continue': bool(include_continue)})


def request_hub_setup():
    """Reset only the setup completion flag, preserving all personal layout/data."""
    state=read('hub-setup.json',{})
    state.update(completed=False,reason='widget-cache-reset',requested_at=time.time())
    write('hub-setup.json',state)



def apply_widget_cache_reset(rpc=None):
    """Clear cached discovery results and row snapshots before Widgets starts.

    Never delete layout.json, settings.xml, watch progress, favourites, or
    installed artwork. Keep the request on disk if deletion partially fails.
    """
    marker = ROOT / WIDGET_RESET_MARKER
    if not marker.is_file():
        return 0
    request = read(WIDGET_RESET_MARKER, {})
    filenames = {
        'widget-cache.json', 'widget-cache.json.bak',
        'smart-cache-setup-v1.json', 'smart-cache-setup-v1.json.bak',
        'widget-preload-state.json', 'widget-preload-state.json.bak',
    }
    # Continue Watching is managed exclusively by Hybrid; a widget reset
    # must never create or mutate another hidden-history list.

    snapshot_re = re.compile(r'^row-[A-Za-z0-9_-]+-[0-9]+\.json(?:\.bak)?$')
    removed = 0
    try:
        for candidate in ROOT.iterdir():
            if not candidate.is_file():
                continue
            if candidate.name in filenames or snapshot_re.fullmatch(candidate.name):
                candidate.unlink()
                removed += 1
        request_hub_setup()
        # Remove the request only after successful cleanup and setup scheduling.
        marker.unlink()
        (ROOT / (WIDGET_RESET_MARKER + '.bak')).unlink(missing_ok=True)
    except OSError as exc:
        xbmc.log('[Nebelflix] Widget cache reset deferred: %s' % exc, xbmc.LOGWARNING)
        raise
    with PROVIDER_CACHE_LOCK:
        PROVIDER_CACHE.clear()
    xbmc.log('[Nebelflix] Widget cache reset: %d stored files cleared' % removed, xbmc.LOGINFO)
    return removed

PRELOAD_STATE = 'widget-preload-state.json'


def preload_requested(cache_reset=False, missing=0, total=0):
    """Full-screen preparation is for a fresh/explicit reset, not routine TTL refresh.

    Incomplete initial setups are retried at the next launch. Once completed,
    stale content stays visible while the ordinary warm queue refreshes it.
    """
    if cache_reset:
        return True
    state = read(PRELOAD_STATE, {})
    if state.get('completed_at'):
        return False
    if not total:
        return False
    return missing >= max(1, min(8, (total + 4) // 5))


def preload_started(count):
    write(PRELOAD_STATE, {'started_at': time.time(), 'planned': int(count),
                          'completed_at': 0})


def preload_finished(count, failed=0):
    """Do not falsely mark the cache complete when any rows failed to load."""
    write(PRELOAD_STATE, {'started_at': read(PRELOAD_STATE, {}).get('started_at', 0),
                          'completed_at': time.time() if not failed else 0,
                          'planned': int(count), 'failed': int(failed)})


def restore_bulk_continue():
    """Undo bulk hiding without restoring manually dismissed entries."""
    bulk = set(read('continue-bulk-hidden.json', []))
    if not bulk:
        return 0
    hidden = set(read('continue-hidden.json', []))
    write('continue-hidden.json', sorted(hidden - bulk))
    write('continue-bulk-hidden.json', [])
    return len(bulk)

def layout():
    return read('layout.json', json.loads((SKIN / 'resources/default-layout.json').read_text(encoding='utf-8')))


# Standard TMDb genre identifiers: movie and television genre trees differ.
MOVIE_GENRES = (
    ('Actionfilme', 28), ('Abenteuerfilme', 12), ('Animationsfilme', 16),
    ('Komödien', 35), ('Krimis', 80), ('Dokumentarfilme', 99),
    ('Dramen', 18), ('Familienfilme', 10751), ('Fantasyfilme', 14),
    ('Historienfilme', 36), ('Horrorfilme', 27), ('Musikfilme', 10402),
    ('Mysteryfilme', 9648), ('Liebesfilme', 10749), ('Science-Fiction-Filme', 878),
    ('Thriller', 53), ('Kriegsfilme', 10752), ('Western', 37),
    ('TV-Filme', 10770),
)
TV_GENRES = (
    ('Action- & Abenteuerserien', 10759), ('Animationsserien', 16),
    ('Comedyserien', 35), ('Krimiserien', 80), ('Dokuserien', 99),
    ('Dramaserien', 18), ('Familienserien', 10751), ('Kinderserien', 10762),
    ('Mystery-Serien', 9648), ('Nachrichtensendungen', 10763),
    ('Realityshows', 10764), ('Sci-Fi- & Fantasyserien', 10765),
    ('Soap-Serien', 10766), ('Talkshows', 10767),
    ('Kriegs- & Politikserien', 10768), ('Westernserien', 37),
)
# Home gets the complete union of all film and TV genres, without redundant
# 1:1 duplicate rows. Where both media types have a matching genre, combine
# the two separately queried catalogues in the same row.
HOME_GENRES = (
    ('Action', 28, 10759), ('Abenteuer', 12, 10759),
    ('Animation', 16, 16), ('Komödie', 35, 35), ('Krimi', 80, 80),
    ('Dokumentationen', 99, 99), ('Drama', 18, 18),
    ('Familie', 10751, 10751), ('Fantasy', 14, 10765),
    ('Geschichte', 36, None), ('Horror', 27, None),
    ('Musik', 10402, None), ('Mystery', 9648, 9648),
    ('Romantik', 10749, None), ('Science Fiction', 878, 10765),
    ('Thriller & Nervenkitzel', 53, 9648),
    ('Krieg & Politik', 10752, 10768), ('Western', 37, 37),
    ('Kinder', None, 10762), ('Nachrichten', None, 10763),
    ('Reality', None, 10764), ('Soap', None, 10766),
    ('Talkshows', None, 10767), ('TV-Filme', 10770, None),
)
GENRE_LAYOUT_VERSION = 3


def _genre_rows(page):
    if page == 'movie':
        return [{'label': label, 'path': 'nf://genre/movie/%d' % key}
                for label, key in MOVIE_GENRES]
    if page == 'tv':
        return [{'label': label, 'path': 'nf://genre/tv/%d' % key}
                for label, key in TV_GENRES]
    if page == 'home':
        return [{'label': label, 'path': 'nf://genre/all/%s/%s' % (
            m if m is not None else 0, t if t is not None else 0)}
                for label, m, t in HOME_GENRES]
    return []


def _old_genre_path(path):
    if path.startswith('nf://genre/'):
        return True
    params = parse_qs(urlparse(path).query)
    if 'with_genres' in params:
        return True
    if 'action' in params and params.get('action') == ['listings']:
        url = (params.get('url') or [''])[0]
        return 'with_genres=' in url
    return False


def migrate_genre_layout(data):
    """Merge genre rows into the main tabs, keeping personal rows and providers."""
    if not isinstance(data, dict) or not isinstance(data.get('tabs'), list):
        return False
    if data.get('genre_layout_version') == GENRE_LAYOUT_VERSION:
        return False
    tabs = data['tabs']
    provider_rows = []
    for tab in tabs:
        if isinstance(tab, dict) and tab.get('id') == 'streaming':
            provider_rows += [r for r in tab.get('rows', []) if isinstance(r, dict)
                              and str(r.get('path', '')).startswith('nf://provider/')]
    tabs[:] = [t for t in tabs if isinstance(t, dict)
               and t.get('id') not in ('genres', 'streaming')]
    for tab in tabs:
        page = tab.get('id')
        if page not in ('home', 'movie', 'tv'):
            continue
        base_rows = [r for r in tab.get('rows', []) if isinstance(r, dict)
                     and not _old_genre_path(str(r.get('path', '')))]
        # Keep provider widgets formerly owned by the retired streaming tab.
        # Route films/series to the corresponding existing tab; mixed to Home.
        known = {r.get('path') for r in base_rows}
        for item in provider_rows:
            path = item.get('path', '')
            target = {'movie': 'movie', 'tv': 'tv', 'all': 'home'}.get(
                path[len('nf://provider/'):].split('/', 1)[0])
            if target == page and path not in known:
                base_rows.append(item)
                known.add(path)
        # The first few editorial rows remain at the top, with genre rows
        # immediately after them rather than below all the providers.
        split = min(4, len(base_rows))
        tab['rows'] = (base_rows[:split] + _genre_rows(page) + base_rows[split:])[:MAX_ROWS]
    data['genre_layout_version'] = GENRE_LAYOUT_VERSION
    return True


def ensure_genre_layout():
    """One-time non-destructive upgrade, keeping a .bak of personal layout."""
    profile = ROOT / 'layout.json'
    if not profile.exists():
        return False  # Bundled default has the new structure already.
    data = read('layout.json', {})
    if not migrate_genre_layout(data):
        return False
    write('layout.json', data)
    return True



# Reorder only the three requested tabs.  Never delete a row: installed skins
# may have personalized layout.json files that override default-layout.json.
TOP10_LAYOUT_VERSION = 1


def migrate_top10_priority(data):
    """Move the existing Top 10 directly above Continue Watching, once.

    Do not touch other tabs or user-created rows. The one-time marker means
    people can freely reorder their widgets again after this update.
    """
    if not isinstance(data, dict) or not isinstance(data.get('tabs'), list):
        return False
    if data.get('top10_layout_version') == TOP10_LAYOUT_VERSION:
        return False
    for tab in data['tabs']:
        if not isinstance(tab, dict) or tab.get('id') not in ('home', 'movie', 'tv'):
            continue
        rows = tab.get('rows')
        if not isinstance(rows, list):
            continue
        # The stable catalog path also matches when the original heading was
        # renamed by the user.  Prefer the specifically configured Top 10.
        path_hint = {
            'home': 'catalogue/movie/popular',
            'movie': 'catalogue/movie/top_rated',
            'tv': 'catalogue/tv/top_rated',
        }[tab['id']]
        from urllib.parse import unquote
        def is_top10(row):
            return (isinstance(row, dict) and
                    (str(row.get('label', '')).strip().casefold().startswith('top 10') or
                     path_hint in unquote(str(row.get('path', '')))))
        def is_continue(row):
            return isinstance(row, dict) and str(row.get('path', '')).startswith('nf://continue')
        top_index = next((i for i, row in enumerate(rows) if is_top10(row)), None)
        continue_index = next((i for i, row in enumerate(rows) if is_continue(row)), None)
        if top_index is None or continue_index is None:
            continue
        top_row = rows.pop(top_index)
        continue_index = next(i for i, row in enumerate(rows) if is_continue(row))
        rows.insert(continue_index, top_row)
    # Mark even if a user deleted one of these rows, to respect later edits.
    data['top10_layout_version'] = TOP10_LAYOUT_VERSION
    return True


def ensure_top10_priority():
    """Upgrade an already saved layout, retaining a backup and every row."""
    profile = ROOT / 'layout.json'
    if not profile.exists():
        return False  # New installations use bundled default-layout.json.
    data = read('layout.json', {})
    if not migrate_top10_priority(data):
        return False
    write('layout.json', data)  # write() creates layout.json.bak first.
    return True




NEBELFLIX_TV_LAYOUT_VERSION = 1


def migrate_nebelflix_tv_layout(data):
    """Replace only legacy LiveTV row links; leave all user-created widgets intact."""
    if not isinstance(data,dict) or not isinstance(data.get('tabs'),list):
        return False
    if data.get('nebelflix_tv_layout_version')==NEBELFLIX_TV_LAYOUT_VERSION:
        return False
    changed=False
    for tab in data['tabs']:
        if not isinstance(tab,dict) or tab.get('id')!='live_tv':continue
        rows=tab.get('rows',[])
        if not isinstance(rows,list):continue
        oldpaths=('nf://moviescout/germany','nf://vavoo/groups',
                  'nf://moviescout/live','nf://moviescout/other')
        for row in rows:
            if isinstance(row,dict) and row.get('path') in oldpaths:
                row['path']='nf://nebelflix/tv'
                row['label']='TV Gruppen'
                changed=True
        if not any(isinstance(row,dict) and row.get('path')=='nf://nebelflix/tv' for row in rows):
            rows.insert(0,{'label':'TV Gruppen','path':'nf://nebelflix/tv'})
            changed=True
        break
    data['nebelflix_tv_layout_version']=NEBELFLIX_TV_LAYOUT_VERSION
    return True


def ensure_nebelflix_tv_layout():
    profile=ROOT/'layout.json'
    if not profile.exists():return False
    data=read('layout.json',{})
    if not migrate_nebelflix_tv_layout(data):return False
    write('layout.json',data)
    # Retire only the old TV row snapshots and cached provider results.
    # No personal layout, watch progress, movies or series row is removed.
    cached=read('widget-cache.json',{})
    if isinstance(cached,dict):
        stale=[k for k in cached if str(k).startswith(('nf://moviescout/germany',
                             'nf://vavoo/groups','nf://moviescout/live','nf://moviescout/other'))]
        for key in stale:cached.pop(key,None)
        if stale:write('widget-cache.json',cached)
    for file in ROOT.glob('row-live_tv-*.json*'):
        if file.is_file():
            try:file.unlink()
            except OSError:pass
    return True


def row_snapshot_name(page, cid):
    """Give every tab its own widget file so delayed Kodi requests cannot cross tabs."""
    safe_page = re.sub(r'[^A-Za-z0-9_-]', '', str(page or 'home'))[:64] or 'home'
    return 'row-%s-%d.json' % (safe_page, int(cid))


def identity(item):
    key = (item.get('Type', '') + ':' + item['TMDB']) if item.get('TMDB') else item.get('Path', '')
    if item.get('DBType') == 'episode':
        key += ':' + item.get('Season', '') + ':' + item.get('Episode', '')
    return key


DIRECT_MEDIA_EXTENSIONS = (
    '.m3u8', '.mpd', '.ism', '.isml', '.mp4', '.m4v', '.mkv', '.avi',
    '.mov', '.wmv', '.webm', '.flv', '.ts', '.mpg', '.mpeg', '.ogv',
    '.3gp', '.vob', '.asf', '.wtv', '.strm'
)


def is_direct_media_source(path):
    """Recognize a resolved playable source, including extensionless HLS/DASH URLs.

    Plugin URLs are deliberately excluded: those are launchers/resolvers and can
    reopen a provider chooser instead of resuming the actual media source.
    """
    value = str(path or '').split('|', 1)[0].strip()
    if not value:
        return False
    parsed = urlparse(value)
    scheme = parsed.scheme.casefold()
    if scheme in ('plugin', 'resource'):
        return False
    media_path = (parsed.path or value).casefold()
    if media_path.endswith(DIRECT_MEDIA_EXTENSIONS):
        return True
    return scheme in ('http', 'https') and any(marker in media_path for marker in (
        '/playlist/', '/manifest/', '/mpd/', '/dash/'
    ))


def same_progress_item(left, right):
    """Match old URL-only entries to later entries with recovered TMDB IDs."""
    if identity(left) == identity(right):
        return True
    if (left.get('Type'), left.get('DBType'), left.get('Season', ''), left.get('Episode', '')) != \
            (right.get('Type'), right.get('DBType'), right.get('Season', ''), right.get('Episode', '')):
        return False
    left_title = ' '.join((left.get('Title') or '').casefold().split())
    right_title = ' '.join((right.get('Title') or '').casefold().split())
    return bool(left_title and left_title == right_title)


def continue_identity(item):
    """Stable identity for dismissing a title even when a host URL expires."""
    if item.get('TMDB'):
        return identity(item)
    title = re.sub(r'[^\w]+', '', (item.get('Title') or '').casefold())
    if item.get('DBType') == 'episode':
        return 'episode:%s:%s:%s' % (title, item.get('Season', ''), item.get('Episode', ''))
    if title:
        return '%s:%s' % (item.get('Type', ''), title)
    return identity(item)


def navigation(file):
    label = re.sub(r'\[[^\]]*\]', '', file.get('label', '')).strip().lower().rstrip('.… ')
    return label in ('mehr', 'weiter', 'next', 'next page', 'nächste seite', 'naechste seite', 'more') or label.startswith(('nächste seite ', 'next page '))


def from_file(file):
    path = file.get('file') or file.get('windowparameter') or file.get('path') or ''
    params = parse_qs(urlparse(path).query)
    media = (params.get('tmdb_type') or [''])[0]
    dbtype = file.get('type', '')
    if not media:
        media = 'tv' if dbtype in ('episode', 'tvshow', 'season') else 'movie'
    art = file.get('art') or {}
    uid = file.get('uniqueid') or {}
    return dict(Title=file.get('title') or file.get('label') or '', Plot=file.get('plot', ''),
                Path=path, TMDB=str((params.get('tmdb_id') or [uid.get('tmdb', '')])[0]),
                Type=media, DBType=dbtype, Year=str(file.get('year') or ''),
                ShowTitle=file.get('showtitle', ''),
                Fanart=art.get('fanart') or art.get('landscape') or file.get('thumbnail', ''),
                Poster=art.get('poster') or file.get('thumbnail', ''), Clearlogo=art.get('clearlogo') or art.get('tvshow.clearlogo') or art.get('season.clearlogo', ''),
                Genres=' / '.join(file.get('genre') or []), Rating=str(file.get('rating') or ''),
                Season=str(file.get('season', '')), Episode=str(file.get('episode', '')),
                Folder=file.get('filetype') == 'directory', Resume=(file.get('resume') or {}).get('position', 0),
                Total=(file.get('resume') or {}).get('total', 0),
                Duration=('%d Min.' % (file['runtime']//60)) if file.get('runtime') else '', Channel=file.get('channelid'))


def mylist_identity(item):
    """Stable item identifier: IMDb/TMDb media and episode boundaries matter."""
    media = str(item.get('Type') or '').strip().lower()
    tmdb = str(item.get('TMDB') or '').strip()
    if media == 'tvshow':
        media = 'tv'
    kind = str(item.get('DBType') or '').lower()
    suffix = ''
    if kind == 'episode':
        suffix = ':%s:%s' % (item.get('Season', ''), item.get('Episode', ''))
    if tmdb.isdigit() and media in ('movie', 'tv'):
        return '%s:%s%s' % (media, tmdb, suffix)
    path = str(item.get('Path') or '').strip()
    if path:
        return 'path:' + path
    # Old entries may have only title/year; avoid empty identities.
    title = str(item.get('Title') or '').strip().casefold()
    if title:
        return 'title:%s:%s:%s%s' % (media, title, item.get('Year', ''), suffix)
    return ''


MYLIST_MIGRATION = 'mylist-v2-migrated.json'


def read_mylist(rpc=None):
    """Read one canonical list, with optional one-time legacy Kodi import.

    Never merge all Kodi favourites on every refresh: deleted items otherwise
    reappear, and Kodi's own favourites can include unrelated addons.
    """
    saved = read('mylist.json', [])
    if not isinstance(saved, list):
        saved = []
    # A present legacy list (even an intentionally empty list) is authoritative.
    # The .bak file counts too: never revive a user-deleted favourite.
    if ((ROOT / 'mylist.json').exists() or (ROOT / 'mylist.json.bak').exists()
            or (ROOT / MYLIST_MIGRATION).exists() or rpc is None):
        return saved
    try:
        favs = rpc('Favourites.GetFavourites', {
            'properties': ['path', 'windowparameter', 'thumbnail']
        }).get('favourites') or []
    except Exception as exc:
        xbmc.log('[Nebelflix] Meine Liste: legacy import postponed: %s' % exc, xbmc.LOGWARNING)
        return saved
    known = set()
    imported = []
    for fav in favs:
        if not isinstance(fav, dict):
            continue
        route = str(fav.get('windowparameter') or fav.get('path') or '')
        if not route.startswith('plugin://plugin.video.nebelflix.hybrid/'):
            continue
        entry = from_file(dict(fav, label=fav.get('title') or ''))
        key = mylist_identity(entry)
        if key and key not in known:
            imported.append(entry)
            known.add(key)
    # Persist an empty list too, so a later Kodi favourites action cannot
    # silently repopulate items removed from Nebelflix.
    write('mylist.json', imported)
    write(MYLIST_MIGRATION, {'at': time.time(), 'imported': len(imported)})
    return imported


def playback_item(file, path):
    """Recover movie/episode identity from Kodi labels when a player add-on
    opens a raw host URL and drops the original Nebelflix Hybrid item metadata."""
    item = from_file(file)
    item['Path'] = path or item.get('Path', '')

    # Nebelflix Hybrid keeps the selected media identity while resolving a hoster,
    # even when the hoster replaces Kodi's current ListItem with a bare URL.
    # Use that record as the authoritative identity for Continue Watching.
    window = xbmcgui.Window(10000)
    try:
        helper = json.loads(window.getProperty('xShip.PlayerInfoString') or '{}')
    except (TypeError, ValueError):
        helper = {}
    helper_type = str(helper.get('tmdb_type') or '').lower()
    helper_id = str(helper.get('tmdb_id') or '')
    if helper_id.isdigit() and helper_type in ('movie', 'tv', 'episode'):
        if helper_type == 'movie':
            item.update(Type='movie', DBType='movie', TMDB=helper_id)
        else:
            item.update(Type='tv', DBType='episode', TMDB=helper_id,
                        Season=str(helper.get('season') or item.get('Season') or ''),
                        Episode=str(helper.get('episode') or item.get('Episode') or ''))
            if not item.get('ShowTitle'):
                item['ShowTitle'] = xbmc.getInfoLabel('VideoPlayer.TVShowTitle').strip()

    def usable(value):
        value = str(value or '').strip()
        return value and value.casefold() not in ('nicht verfügbar', 'not available', 'unknown', 'n/a', 'none')

    # The detail dialog still holds the selected show's rich metadata. Reuse
    # it only when its TMDB ID matches the item now playing.
    try:
        detail = json.loads(window.getProperty('NFD.Item') or '{}')
    except (TypeError, ValueError):
        detail = {}
    if item.get('TMDB') and detail.get('TMDB') == item['TMDB']:
        for key in ('Plot', 'Year', 'Genres', 'Rating', 'Duration', 'Fanart', 'Poster', 'Clearlogo'):
            if not usable(item.get(key)) and usable(detail.get(key)):
                item[key] = detail[key]

    content = xbmc.getInfoLabel('VideoPlayer.Content').strip().lower()
    label_title = xbmc.getInfoLabel('VideoPlayer.Title').strip()
    title = item.get('Title', '') if usable(item.get('Title')) else label_title
    if not usable(title) or title.casefold().endswith(DIRECT_MEDIA_EXTENSIONS):
        title = ''
    showtitle = (file.get('showtitle') or xbmc.getInfoLabel('VideoPlayer.TVShowTitle')).strip()
    season_value = file.get('season')
    if season_value in (None, '', -1, '-1'):
        season_value = xbmc.getInfoLabel('VideoPlayer.Season')
    episode_value = file.get('episode')
    if episode_value in (None, '', -1, '-1'):
        episode_value = xbmc.getInfoLabel('VideoPlayer.Episode')
    season = str(season_value if season_value not in (None, '', -1, '-1') else '').strip()
    episode = str(episode_value if episode_value not in (None, '', -1, '-1') else '').strip()
    uniqueid = file.get('uniqueid') or {}
    tmdb = str(uniqueid.get('tmdb') or xbmc.getInfoLabel('VideoPlayer.UniqueID(tmdb)') or '')
    if not tmdb.isdigit():
        tmdb = helper_id if helper_id.isdigit() else ''

    # Some resolver add-ons expose plot and technical metadata through Kodi's
    # VideoPlayer labels while their ListItem fields are incomplete.
    for key, label in (('Plot', 'VideoPlayer.Plot'), ('Year', 'VideoPlayer.Year'),
                       ('Genres', 'VideoPlayer.Genre'), ('Rating', 'VideoPlayer.Rating'),
                       ('Duration', 'VideoPlayer.Duration')):
        if not item.get(key):
            value = xbmc.getInfoLabel(label).strip()
            if usable(value):
                item[key] = value

    if showtitle or content in ('episode', 'episodes') or (item.get('DBType') == 'episode' and item.get('Season') and item.get('Episode')):
        episode_title = title
        if not episode_title or episode_title.casefold().endswith(DIRECT_MEDIA_EXTENSIONS):
            episode_title = item.get('ShowTitle') or showtitle or 'TV-Folge'
            try:
                episode_title += ' · S%02dE%02d' % (int(item.get('Season') or season), int(item.get('Episode') or episode))
            except (TypeError, ValueError):
                pass
        item.update(Type='tv', DBType='episode', Title=title or item.get('Title', ''),
                    ShowTitle=showtitle or item.get('ShowTitle', ''),
                    Season=season or item.get('Season', ''), Episode=episode or item.get('Episode', ''), TMDB=tmdb or item.get('TMDB', ''))
        if not usable(item.get('Title')) or item['Title'].casefold().endswith(DIRECT_MEDIA_EXTENSIONS):
            item['Title'] = episode_title
    elif content in ('movie', 'movies') and title and not title.casefold().endswith(DIRECT_MEDIA_EXTENSIONS):
        item.update(Type='movie', DBType='movie', Title=title, TMDB=tmdb)
    elif item.get('TMDB'):
        item['DBType'] = 'movie' if item.get('Type') == 'movie' else 'episode'
    return item


def listitem(item):
    li = xbmcgui.ListItem(label=item.get('Title', ''), path=item.get('Path', ''))
    artwork = item.get('Fanart') or item.get('Poster') or 'special://skin/media/nebelflix/poster.png'
    li.setArt({'fanart': artwork, 'landscape': artwork,
               'thumb': artwork, 'poster': item.get('Poster', ''),
               'clearlogo': item.get('Clearlogo', '')})
    li.setProperty('NF.Data', json.dumps(item, ensure_ascii=False))
    li.setProperty('tmdb_id', item.get('TMDB', ''))
    li.setProperty('tmdb_type', item.get('Type', ''))
    category = bool(item.get('GuideOpen') or (item.get('AddonBrowse')
        and str(item.get('Path','')).startswith('plugin://plugin.video.nebelflix.tv/?action=list&group=')))
    li.setProperty('NFTV.Category', '1' if category else '')
    li.setProperty('NF.EPG', item.get('EPG', ''))
    li.setProperty('NF.EPGNext', item.get('EPGNext', ''))
    li.setProperty('NF.EPGProgress', str(item.get('EPGProgress', 0)))
    if item.get('AddonLauncher'):
        li.setProperty('NF.AddonLauncher', 'true')
        li.setProperty('NF.AddonID', item.get('AddonID', ''))
    tag = li.getVideoInfoTag()
    tag.setTitle(item.get('Title', '')); tag.setPlot(item.get('Plot', ''))
    if item.get('DBType') in ('movie', 'tvshow', 'episode', 'season'):
        tag.setMediaType(item['DBType'])
    if item.get('Resume'):
        li.setProperty('ResumeTime', str(item['Resume']))
        li.setProperty('TotalTime', str(item.get('Total', 0)))
    return li


def xship_widget_url(url, page=1):
    if not url.startswith('plugin://plugin.video.nebelflix.hybrid/'):
        return url
    query = {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}
    info = query.pop('info', '')
    media = query.pop('tmdb_type', 'movie')
    allowed = ('trending_day', 'trending_week', 'popular', 'top_rated', 'upcoming',
               'now_playing', 'on_the_air', 'airing_today', 'discover')
    if info not in allowed:
        return url
    query.pop('with_id', None)
    query.pop('page', None)
    if info.startswith('trending_'):
        endpoint = 'trending/' + media + '/' + info.split('_')[1]
    elif info == 'discover':
        endpoint = 'discover/' + media
    else:
        endpoint = media + '/' + info
    return 'plugin://plugin.video.nebelflix.hybrid/?' + urlencode({
        'action': 'listings', 'media_type': media, 'next_pages': page or 1,
        'url': 'catalogue/' + endpoint + ('?' + urlencode(query) if query else '')})

def _tmdb_directory(rpc, url, page=None):
    url = xship_widget_url(url, page)
    direct=fast_catalog.fetch(url)
    if direct is not None:
        return direct
    params = {'directory': url, 'media': 'video',
              'properties': ['title', 'plot', 'year', 'rating', 'genre', 'runtime', 'art',
                            'thumbnail', 'uniqueid', 'season', 'episode', 'resume']}
    if page and page > 1 and 'next_pages=' not in url:
        params['directory'] += ('&' if '?' in url else '?') + urlencode({'page': page})
    result = rpc('Files.GetDirectory', params)
    items, has_next = [], False
    for file in result.get('files') or []:
        if navigation(file):
            has_next = True
        elif file.get('label') != '..':
            item = from_file(file)
            if item.get('TMDB') and item.get('Type') in ('movie', 'tv'):
                items.append(item)
    return items, has_next


def _provider_catalogue(rpc, media):
    now = time.time()
    with PROVIDER_CACHE_LOCK:
        cached = PROVIDER_CACHE.get(media)
        if cached and now - cached[0] < 1800:
            return cached[1]
    url = 'plugin://plugin.video.nebelflix.hybrid/?' + urlencode({
        'info': 'watch_providers', 'tmdb_type': media})
    result = rpc('Files.GetDirectory', {'directory': url, 'media': 'video',
        'properties': ['title', 'art', 'thumbnail']})
    providers = [(file.get('label', ''), file.get('file', ''))
                 for file in (result.get('files') or [])
                 if file.get('label') != '..' and file.get('file')]
    with PROVIDER_CACHE_LOCK:
        PROVIDER_CACHE[media] = (now, providers)
    return providers


def _provider_matches(label, slug):
    normalized = re.sub(r'[^a-z0-9+]+', ' ', (label or '').casefold()).strip()
    return any(normalized == alias or normalized.startswith(alias + ' ')
               for alias in PROVIDER_MATCH.get(slug, (slug,)))


def _provider_items(rpc, media, slug, page=1):
    items, has_next = [], False
    for name, route in _provider_catalogue(rpc, media):
        if not _provider_matches(name, slug):
            continue
        query = {key: values[0] for key, values in parse_qs(urlparse(route).query).items()}
        query['url'] = 'catalogue/discover/' + media + '?' + query.get('url', '') + '&with_watch_monetization_types=flatrate'
        query['next_pages'] = page
        route = 'plugin://plugin.video.nebelflix.hybrid/?' + urlencode(query)
        found, next_page = _tmdb_directory(rpc, route, page)
        items.extend(found)
        has_next = has_next or next_page
    unique, seen = [], set()
    for item in items:
        key = identity(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique, has_next


def _special_directory(rpc, path):
    parts = path[len('nf://'):].split('/')
    if len(parts) >= 3 and parts[0] == 'genre':
        media = parts[1]
        if media not in ('all', 'movie', 'tv'):
            return [], ''
        if media == 'all':
            if len(parts) < 4 or not all(p.isdigit() for p in parts[2:4]):
                return [], ''
            genre_ids = [('movie', int(parts[2])), ('tv', int(parts[3]))]
            page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 1
            prefix = '/'.join(parts[:4])
        else:
            if not parts[2].isdigit():
                return [], ''
            genre_ids = [(media, int(parts[2]))]
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
            prefix = '/'.join(parts[:3])
        if page < 1 or page > 500:
            return [], ''
        items, has_next = [], False
        for kind, genre in genre_ids:
            if not genre:
                continue
            path = 'plugin://plugin.video.nebelflix.hybrid/?' + urlencode({
                'action': 'listings', 'media_type': kind, 'next_pages': page,
                'url': 'catalogue/discover/%s?with_genres=%s&sort_by=popularity.desc' % (kind, genre)})
            found, more = _tmdb_directory(rpc, path, page)
            items.extend(found)
            has_next = has_next or more
        unique, seen = [], set()
        for item in items:
            key = identity(item)
            if key not in seen:
                unique.append(item)
                seen.add(key)
        # One reproducible shuffle per day/genre/page; navigating away and
        # back never moves a previously selected title unexpectedly.
        from datetime import date
        random.Random('%s:%s:%s' % (date.today().isoformat(), prefix, page)).shuffle(unique)
        return unique, ('nf://' + prefix + '/' + str(page + 1)) if has_next else ''
    if parts[:2] == ['random', 'all']:
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        items, has_next = [], False
        for media in ('movie', 'tv'):
            url = 'plugin://plugin.video.nebelflix.hybrid/?' + urlencode({
                'info': 'trending_week', 'tmdb_type': media, 'with_id': 'true'})
            found, more = _tmdb_directory(rpc, url, page)
            items.extend(found)
            has_next = has_next or more
        random.shuffle(items)
        return items, ('nf://random/all/%s' % (page + 1)) if has_next else ''
    if len(parts) >= 3 and parts[0] == 'provider':
        media, slug = parts[1], parts[2]
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        if media not in ('all', 'movie', 'tv') or slug not in PROVIDER_MATCH:
            return [], ''
        media_types = ('movie', 'tv') if media == 'all' else (media,)
        items, has_next = [], False
        for kind in media_types:
            found, more = _provider_items(rpc, kind, slug, page)
            items.extend(found)
            has_next = has_next or more
        unique, seen = [], set()
        for item in items:
            key = identity(item)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique, ('nf://provider/%s/%s/%s' % (media, slug, page + 1)) if has_next else ''
    return None


def directory(rpc, path):
    if path in ('nf://nebelflix/tv','nf://moviescout/germany'):
        return nebelflix_tv.directory(rpc, path)
    if path in ('nf://moviescout/live','nf://moviescout/other'):
        return nebelflix_tv.directory(rpc, 'nf://nebelflix/tv')
    parsed = urlparse(path)
    if parsed.scheme == 'nf' and parsed.netloc == 'addons':
        addon_type = parsed.path.strip('/').split('/', 1)[0]
        types = {
            'video': [('video', 'xbmc.addon.video')],
            'music': [('music', 'xbmc.addon.audio')],
            'all': [('video', 'xbmc.addon.video'), ('music', 'xbmc.addon.audio')],
        }
        if addon_type not in types:
            return [], ''
        selected = set((parse_qs(parsed.query).get('ids') or [''])[0].split(','))
        items = []
        for media_type, kodi_type in types[addon_type]:
            response = rpc('Addons.GetAddons', {
                'type': kodi_type, 'enabled': True,
                'properties': ['name', 'thumbnail', 'fanart', 'summary']})
            for addon in response.get('addons') or []:
                addon_id = str(addon.get('addonid') or '')
                if not addon_id or selected and addon_id not in selected:
                    continue
                icon = addon.get('thumbnail') or ('special://home/addons/%s/icon.png' % addon_id)
                items.append({
                    'Title': addon.get('name') or addon_id,
                    'Plot': addon.get('summary') or '',
                    'Path': 'nf://addon/' + addon_id,
                    'AddonID': addon_id, 'AddonLauncher': True,
                    'Fanart': addon.get('fanart') or icon, 'Poster': icon,
                    'Clearlogo': '', 'Type': media_type, 'DBType': 'addon',
                })
        items.sort(key=lambda item: item.get('Title', '').casefold())
        return items, ''
    if path.startswith(('nf://random/', 'nf://provider/', 'nf://genre/')):
        result = _special_directory(rpc, path)
        if result is not None:
            return result
    if path == 'nf://vavoo/groups' or path.startswith('nf://vavoo/group?'):
        return vavoo_live.directory(path)
    if path == 'nf://mylist':
        return read_mylist(rpc), ''
    if path.startswith('nf://continue'):
        media = path.rsplit('/', 1)[-1]
        # The Hybrid playcount DB is the only progress source.
        # No Kodi VideoLibrary or skin progress.json merging.
        return history_access.continue_items(media), ''
    catalog_url=xship_widget_url(path)
    direct=fast_catalog.fetch(catalog_url)
    if direct is not None:
        items,more=direct
        query={key:values[0] for key,values in parse_qs(urlparse(catalog_url).query).items()}
        query['next_pages']=str(int(query.get('next_pages','1'))+1)
        return items,('plugin://plugin.video.nebelflix.hybrid/?'+urlencode(query)) if more else ''
    result = rpc('Files.GetDirectory', {'directory': path, 'media': 'video',
        'properties': ['title', 'plot', 'year', 'rating', 'genre', 'runtime', 'art', 'thumbnail',
                       'uniqueid', 'season', 'episode', 'resume']})
    items, next_path = [], ''
    for f in result.get('files') or []:
        if navigation(f):
            next_path = f['file']
        elif f.get('label') != '..':
            items.append(from_file(f))
    return items, next_path


def dismiss_continue(item):
    """Request deletion in Hybrid and wait for acknowledgement before reporting success.

    Kodi's RunPlugin is asynchronous. Reporting success immediately caused the
    skin to re-read the old row and show it again after a widget refresh.
    """
    url = history_access.delete_url(item)
    if not url:
        return False
    from urllib.parse import urlencode
    import uuid
    request = uuid.uuid4().hex
    media = str(item.get('HybridMedia') or '')
    identity = str(item.get('HybridIdentity') or '')
    try:
        season = int(item.get('Season') or 0)
        episode = int(item.get('Episode') or 0)
    except (TypeError, ValueError):
        return False
    # No removals while a stream is active: onPlayBackStopped may rewrite progress.
    if xbmc.Player().isPlayingVideo():
        xbmcgui.Dialog().ok('Nebelflix', 'Bitte die laufende Wiedergabe zuerst beenden.')
        return False
    window = xbmcgui.Window(10000)
    window.clearProperty('NF.HistoryRemoveAck')
    url += '&' + urlencode({'request': request})
    xbmc.executebuiltin('RunPlugin(%s)' % url)
    deadline = time.monotonic() + 8.0
    monitor = xbmc.Monitor()
    while not monitor.abortRequested() and time.monotonic() < deadline:
        message = window.getProperty('NF.HistoryRemoveAck')
        if message:
            try:
                ack = json.loads(message)
                if ack.get('request') == request:
                    if ack.get('success'):
                        # Positive acknowledgement and source-of-truth check.
                        return history_access.contains(media, identity, season, episode) is False
                    xbmc.log('[Nebelflix] Entfernen aus Weiterschauen: ' +
                             str(ack.get('error') or 'fehlgeschlagen'), xbmc.LOGWARNING)
                    return False
            except (TypeError, ValueError):
                pass
        monitor.waitForAbort(0.1)
    xbmc.log('[Nebelflix] History context action: timeout waiting for Hybrid', xbmc.LOGWARNING)
    return False


def restore_continue(item):
    # The Hybrid database is authoritative; no independent skin hide list.
    return None


def resume_record(item):
    """Persist the playback identity and position without a resolved host URL."""
    record = dict(item)
    record.pop('StreamURL', None)
    ident = str(record.get('TMDB') or '')
    media = record.get('Type')
    path = record.get('Path') or ''
    if ident and media in ('movie', 'tv') and is_direct_media_source(path):
        query = dict(info='play', tmdb_type=media, tmdb_id=ident)
        if record.get('DBType') == 'episode':
            query.update(season=record.get('Season', ''), episode=record.get('Episode', ''))
        record['Path'] = 'plugin://plugin.video.nebelflix.hybrid/?' + urlencode(query)
    return record


def _continue_item(item):
    """Exclude completed titles using the same threshold as playback tracking."""
    try:
        position = float(item.get('Resume') or 0)
        total = float(item.get('Total') or 0)
    except (TypeError, ValueError):
        return False
    margin = min(120, total * 0.05) if item.get('DBType') == 'episode' else 600
    return position > 0 and (total <= 0 or total - position > margin)


class Widgets:
    """One independent worker per row; GUI mutations only on the monitor thread."""
    def __init__(self, rpc, win, prop):
        self.rpc, self.win, self.prop = rpc, win, prop
        self.results = queue.Queue()
        self.generation = 0
        self.rows = {}
        self.budget = performance.Budget(read('performance.json', {}).get('profile', 'auto'))
        self.paused = False
        self.next_warm_at = 0
        self.spotlight_stamp = None
        self.spotlight_dirty = True
        self.hubs = {}
        self.active_hub = None
        self.generation_hubs = {}
        self.hub_paths = {}
        self.hub_retry_at = 0
        self.cache = read('widget-cache.json', {})
        if not isinstance(self.cache, dict):
            self.cache = {}
        self.trim_cache()
        self.prop('PerformanceProfile', self.budget.name)
        xbmc.log('[Nebelflix Performance] Profil=%s RAM=%s MB frei=%s MB' %
                 (self.budget.name, self.budget.total, self.budget.free), xbmc.LOGINFO)
        self.inflight = {}
        self.lock = threading.Lock()
        self.selection = {}
        self.page = 'home'
        self.warm_paths = []
        self.warm_scheduled = False
        self.warm_done = set()
        self.warm_current = ''
        self.warm_started = {}
        self.warm_plan = set()
        self.warm_completed = set()
        self.warm_errors = set()
        self.warm_active = set()
        self.cache_dirty = False
        self.cache_write_at = time.monotonic()
        self.logo_pool = ThreadPoolExecutor(max_workers=1)
        self.logo_jobs = {}
        self.logo_cache = read('title-logo-cache.json', {})
        self.logos = {tuple(key.split(':', 1)): entry['url']
                      for key, entry in self.logo_cache.items()
                      if isinstance(entry, dict) and isinstance(entry.get('url'), str)
                      and time.time() - entry.get('time', 0) < (2592000 if entry['url'] else 86400)
                      and ':' in key}
        self.logo_refresh_at = 0
        self.snapshots = {}
        self.artwork = Artwork(ROOT / 'widget-artwork', self.budget.limits['artwork_jobs'],
                               self.budget.limits['artwork_mb'])

    def trim_cache(self):
        limit = self.budget.limits['cache_entries']
        if len(self.cache) > limit:
            self.cache = dict(sorted(self.cache.items(),
                              key=lambda item: item[1].get('time', 0), reverse=True)[:limit])

    def idle(self):
        with self.lock:
            return not self.inflight and not self.warm_active

    def suspend(self):
        # Set the gate BEFORE checking outstanding requests, including requests
        # belonging to an earlier tab generation. They must finish before disable.
        self.paused = True
        self.artwork.paused = True
        return self.idle()

    def resume(self):
        self.paused = False
        self.artwork.paused = self.budget.pressure

    def publish_spotlight(self):
        # These controls used to launch independent, untracked Hybrid invokers.
        # Populate them only from widget data already in memory.
        if not self.spotlight_dirty:
            return
        self.spotlight_dirty = False
        selected = {}
        for cid, media in ((650, 'movie'), (651, 'tv')):
            records = list(self.snapshots.values()) + [record.get('items', []) for record in self.cache.values()]
            items, seen = [], set()
            for record in records:
                for item in record:
                    key = identity(item)
                    if item.get('Type') == media and item.get('TMDB') and key not in seen:
                        seen.add(key)
                        items.append(item)
                        if len(items) == 8:
                            break
                if len(items) == 8:
                    break
            selected[cid] = items
        stamp = tuple((cid, tuple(identity(item) for item in items)) for cid, items in selected.items())
        if stamp == self.spotlight_stamp:
            return
        self.spotlight_stamp = stamp
        for cid, items in selected.items():
            control = self.win.getControl(cid)
            control.reset()
            control.addItems([listitem(dict(item, **{
                field: self.artwork.local(self.budget.image(item.get(field, ''), field))
                for field in ('Fanart', 'Poster', 'Clearlogo')})) for item in items])

    def prepare_warm(self, config):
        """Prepare first pages for every widget with a bounded-size RPC work queue.

        Interleave tabs so Series/LiveTV are not starved behind Home's genres.
        Paths are deduplicated. No GUI controls or playback rules are changed.
        """
        seen, planned = set(), []
        self.hub_paths = {tab.get('id'): {row.get('path') for row in tab.get('rows', [])}
                         for tab in config.get('tabs', [])}
        tabs = [tab.get('rows', [])[:self.budget.limits['warm_rows']] for tab in config.get('tabs', [])]
        for offset in range(max((len(rows) for rows in tabs), default=0)):
            for rows in tabs:
                if offset >= len(rows):
                    continue
                path = str(rows[offset].get('path') or '')
                if not path or path in seen or not widget_cache_ttl(path):
                    continue
                seen.add(path)
                planned.append(path)
        self.warm_plan = set(planned)
        now = time.time()
        self.warm_completed = {path for path in planned
                               if path in self.cache and
                               now - self.cache[path].get('time', 0) < widget_cache_ttl(path)}
        # Keep current inflight requests in the active pool across replans.
        self.warm_active.intersection_update(self.warm_plan)
        self.warm_errors = set()
        self.warm_paths = [path for path in planned
                           if path not in self.warm_completed and path not in self.warm_active]
        self.warm_scheduled = True
        return len(planned)

    def warm_status(self):
        processed = self.warm_completed | self.warm_errors
        return len(processed), len(self.warm_plan), len(self.warm_errors)

    def warm_missing(self):
        return max(0, len(self.warm_plan - self.warm_completed))


    def flush_cache(self):
        if self.cache_dirty:
            write('widget-cache.json', self.cache)
            self.cache_dirty = False
            self.cache_write_at = time.monotonic()

    @staticmethod
    def logo_key(item):
        media, ident = item.get('Type'), str(item.get('TMDB') or '')
        return (media, ident) if media in ('movie', 'tv') and ident.isdigit() else None

    def load_logo(self, key):
        try:
            from multitrailer import tmdb
            import requests
            started=time.monotonic()
            response=requests.get(tmdb.BASE+'/%s/%s/images'%key,
                params={'api_key':tmdb.DEFAULT_TMDB_KEY,'language':'de-DE','include_image_language':'de,en,null'},
                timeout=(2,2))
            response.raise_for_status()
            logos=response.json().get('logos',[])
            load_timing.record('skin','artwork.logo',started)
            for language in ('de','en',None):
                for logo in logos:
                    if logo.get('iso_639_1')==language and logo.get('file_path'):
                        return tmdb.IMG+'/w500'+logo['file_path']
            return ''
        except Exception as exc:
            xbmc.log('[Nebelflix] Widget logo: ' + str(exc), xbmc.LOGWARNING)
            return None

    def refresh_logos(self):
        if time.monotonic() < self.logo_refresh_at:
            return
        ready = {key for key, job in self.logo_jobs.items() if job.done()}
        if not ready:
            return
        for key in ready:
            result = self.logo_jobs.pop(key).result()
            self.logos[key] = result or ''
            if result is not None:
                self.logo_cache[':'.join(key)] = {'url': result, 'time': time.time()}
        self.logo_cache = dict(sorted(self.logo_cache.items(),
                                     key=lambda entry: entry[1].get('time', 0), reverse=True)[:2000])
        write('title-logo-cache.json', self.logo_cache)
        self.logo_refresh_at = time.monotonic() + 2
        self.cache_dirty = True
        for cid in self.rows:
            items = self.snapshots.get(cid, [])
            if any(not item.get('Clearlogo') and self.logos.get(self.logo_key(item))
                   for item in items):
                position = max(0, self.win.getControl(cid).getSelectedPosition())
                self.publish(cid, items, min(position, len(items) - 1))

    def prepare_artwork(self, items):
        if self.paused or self.budget.pressure:
            return
        items = items[:self.budget.limits['artwork_items']]
        if not xbmc.getCondVisibility('Skin.HasSetting(NFHideClearlogo)'):
            for item in items:
                key = self.logo_key(item)
                if not key or item.get('Clearlogo'):
                    continue
                if key in self.logos:
                    item['Clearlogo'] = self.logos[key]
                elif key not in self.logo_jobs and len(self.logo_jobs) < self.budget.limits['logos']:
                    self.logo_jobs[key] = self.logo_pool.submit(self.load_logo, key)
        self.artwork.prepare([dict(item, **{field: self.budget.image(item.get(field, ''), field, tile=True)
                              for field in ('Fanart', 'Poster', 'Clearlogo')}) for item in items])

    def publish(self, cid, items, position=0):
        self.prepare_artwork(items)
        items = [dict(item, Hub=self.page, **{field: self.artwork.local(self.budget.image(item.get(field) or '', field, tile=True))
                             for field in ('Fanart', 'Poster', 'Clearlogo')})
                 for item in items]
        unchanged = self.snapshots.get(cid) == items
        if unchanged and self.prop('RowsPage') == self.page:
            return
        self.snapshots[cid] = items
        self.spotlight_dirty = True
        # Kodi requires a list provider to dispatch XML onclick/context actions.
        # The provider reads only this small local snapshot (no widget imports).
        placeholder = len(items) == 1 and items[0].get('Empty') and not items[0].get('Next')
        if not unchanged and not placeholder:
            write(row_snapshot_name(self.page, cid), items)
        state = self.rows.get(cid, {})
        query = urlencode({'row': cid, 'continue': '1' if state.get('source', '').startswith('nf://continue') else '0',
                           'top10': '1' if state.get('top10') else '0',
                           'placeholder': '1' if placeholder else '0',
                           'page': self.page, 'revision': time.time_ns()})
        url = 'plugin://skin.nebelflix/?' + query
        if cid <= getattr(self, 'bind_limit', 647):
            self.prop('RowURL' + str(cid), url)
        else:
            self.pending_bindings[cid] = url
        self.selection[cid] = (len(items), position)

    def start(self, page, config):
        started = time.monotonic()
        self.prop('RowsPage', '')
        self.generation += 1
        self.selection.clear()
        self.page = page
        self.hub_retry_at = time.monotonic() + 15
        self.bind_limit = 600 + self.budget.limits['initial_rows']
        self.pending_bindings = {}
        self.prop('Row', '')
        if not self.warm_scheduled:
            self.warm_scheduled = True
        tab = next((x for x in config['tabs'] if x['id'] == page), config['tabs'][0])
        signature = tuple((row.get('label'), row.get('path')) for row in tab.get('rows', [])[:MAX_ROWS])
        hub = self.hubs.get(page)
        restored = bool(hub and hub['signature'] == signature)
        if not restored:
            hub = {'signature': signature, 'rows': {}, 'used': time.monotonic()}
            self.hubs[page] = hub
        self.active_hub = hub
        self.rows = hub['rows']
        self.snapshots = hub.setdefault('snapshots', {})
        self.generation_hubs[self.generation] = hub
        hub['used'] = time.monotonic()
        # Keep independent hub states, but bound resident data on sticks.
        limit = 2 if self.budget.name == 'low' else 5
        for name, old in sorted(list(self.hubs.items()), key=lambda pair: pair[1]['used']):
            if len(self.hubs) <= limit:
                break
            if old is not hub and not any(row.get('busy') for row in old['rows'].values()):
                del self.hubs[name]
                self.generation_hubs = {gen: owner for gen, owner in self.generation_hubs.items() if owner is not old}
        for offset in range(MAX_ROWS):
            cid = 600 + offset
            self.prop('RowHeading' + str(cid), '')
            self.prop('RowState' + str(cid), '')
            self.prop('RowURL' + str(cid), '')
            self.prop('RowKind' + str(cid), '')
        for offset, row in enumerate(tab.get('rows', [])[:MAX_ROWS]):
            cid = 600 + offset
            if not restored:
                self.rows[cid] = {'items': [], 'next': '', 'busy': False, 'loaded': False,
                              'source': row['path'], 'visited': set(), 'history': [],
                              'top10': row['label'].lower().startswith('top 10')}
            self.prop('RowHeading' + str(cid), row['label'])
            self.prop('RowKind' + str(cid), 'continue' if row['path'].startswith('nf://continue') else 'top10' if self.rows[cid]['top10'] else 'content')
            self.prop('RowState' + str(cid), '')
            cached = None if row['path'].startswith(('nf://continue', 'nf://mylist')) else self.cache.get(row['path'])
            if restored and self.rows[cid].get('loaded'):
                self.publish(cid, self.page_display(cid))
            elif cached and isinstance(cached.get('items'), list) and offset < self.budget.limits['initial_rows']:
                # Warmed data is already available; never replace it with a
                # placeholder merely because this tab has just been opened.
                self.rows[cid]['items'] = list(cached['items'][:10] if self.rows[cid]['top10'] else cached['items'])
                self.rows[cid]['next'] = '' if self.rows[cid]['top10'] else cached.get('next', '')
                self.rows[cid]['loaded'] = True
                self.publish(cid, self.page_display(cid))
            else:
                placeholder = 'special://skin/media/nebelflix/poster.png'
                self.publish(cid, [{'Title': 'Wird beim Anwählen geladen', 'Path': placeholder,
                                    'Fanart': placeholder, 'Poster': placeholder, 'Empty': True}])
            # Slow foreground requests remain lazy; the shared preparer loads
            # the other paths independently, without changing remote focus.
            # Foreground requests start in tick() under the shared budget.
            self.rows[cid]['eager'] = offset < self.budget.limits['initial_rows']
        self.prop('RowsPage', page)
        load_timing.record('skin', 'hub.restore' if restored else 'hub.prepare', started,
                           hub=page, rows=len(self.rows), profile=self.budget.name)
        xbmc.log('[Nebelflix] Tab %s: showing cached rows, first %s refreshes prioritized' %
                 (page, min(self.budget.limits['initial_rows'], len(tab.get('rows', [])))), xbmc.LOGINFO)

    def finish_hub_switch(self):
        if self.prop('HubSwitching') != 'true':
            return
        required = [cid for cid, row in self.rows.items()
                    if not (row.get('source', '').startswith('nf://continue') and
                            xbmc.getCondVisibility('Skin.HasSetting(NFHideContinue)'))
                    and not (row.get('top10') and
                             xbmc.getCondVisibility('Skin.HasSetting(NFHideTop10)'))
                    ][:1]
        # A provider response from the old hub may arrive after the new one was
        # requested. Kodi may defer off-screen containers: wait for the first
        # enabled row only; each remaining row masks its own stale binding.
        pending = [cid for cid in required if
                   xbmc.getInfoLabel('Container(%d).ListItem.Property(NF.Hub)' % cid) != self.page]
        if pending:
            if time.monotonic() >= self.hub_retry_at:
                self.hub_retry_at = time.monotonic() + 15
                for cid in pending:
                    url = self.prop('RowURL' + str(cid))
                    if url:
                        self.prop('RowURL' + str(cid), url.split('&bind_retry=')[0] +
                                  '&bind_retry=' + str(time.monotonic_ns()))
            return
        self.prop('HubSwitching', 'false')
        try:
            started = float(self.prop('HubRequestedAt') or time.monotonic())
        except ValueError:
            started = time.monotonic()
        load_timing.record('skin', 'hub.visible', started, hub=self.page)

    def page_display(self, cid, error=False):
        state = self.rows[cid]
        display = list(state['items'])
        arrow = 'special://skin/media/nebelflix/back.png'
        next_art = 'special://skin/media/nebelflix/next.png'
        if state['history']:
            display.insert(0, {'Title': 'Zurück', 'Path': arrow, 'Poster': arrow,
                               'Fanart': arrow, 'Back': True, 'Folder': True,
                               'Row': cid, 'Generation': str(self.generation)})
        if state['next'] or error:
            display.append({'Title': 'Erneut versuchen' if error else 'Weiter',
                            'Path': next_art, 'Poster': next_art,
                            'Fanart': next_art, 'Next': True,
                            'Row': cid, 'Generation': str(self.generation),
                            'Retry': bool(error)})
        if not display:
            image = 'special://skin/media/nebelflix/poster.png'
            display.append({'Title': 'Noch keine Inhalte', 'Path': image,
                            'Fanart': image, 'Poster': image, 'Empty': True})
        return display

    def navigate_page(self, cid, back=False, retry=False):
        state = self.rows.get(cid)
        if not state or state['busy']:
            return
        if back:
            if not state['history']:
                return
            previous = state['history'].pop()
            state.update(source=previous['source'], items=previous['items'],
                         next=previous['next'], busy=False, loaded=True)
            self.prop('RowState' + str(cid), '')
            # Restore the previous page without a network request.
            self.publish(cid, self.page_display(cid), previous.get('position', 0))
            return
        if retry:
            self.load(cid, state['source'])
            return
        if not state['next']:
            return
        previous_page = dict(source=state['source'], items=list(state['items']),
                             next=state['next'],
                             position=max(0, self.win.getControl(cid).getSelectedPosition()))
        state['history'].append(previous_page)
        state.update(source=state['next'], items=[], next='', loaded=False)
        self.prop('RowState' + str(cid), 'Nächste Seite wird geladen …')
        self.publish(cid, self.page_display(cid))
        self.load(cid, state['source'])

    def load(self, cid, path, force=False):
        if self.paused:
            return
        with self.lock:
            if path not in self.inflight and len(self.inflight) >= self.budget.limits['requests']:
                return
        state = self.rows.get(cid)
        if not state or state['busy']:
            return
        state['busy'] = True
        state['request_started'] = time.monotonic()
        self.prop('RowState' + str(cid), 'Lädt …')
        generation = self.generation
        cached = (None if path.startswith(('nf://continue', 'nf://mylist'))
                  else self.cache.get(path))
        ttl = widget_cache_ttl(path)
        if cached:
            # Show the last snapshot immediately, even when stale, while the
            # worker refreshes it. This keeps slow Nebelflix Hybrid rows usable.
            state['items'] = list(cached['items'])
            state['next'] = cached.get('next', '')
            state['loaded'] = True
            self.publish(cid, self.page_display(cid))
            if state['items']:
                self.prop('RowState' + str(cid), '')
            if ttl and not force and time.time()-cached.get('time', 0) < ttl:
                state['busy'] = False
                return
        with self.lock:
            if path in self.inflight:
                self.inflight[path].append((generation, cid))
                return
            self.inflight[path] = [(generation, cid)]
        def worker():
            try:
                items, next_path = directory(self.rpc, path)
                error = ''
            except Exception as exc:
                items, next_path, error = [], '', str(exc)
            with self.lock:
                waiters = self.inflight.pop(path, [])
            for gen, row_id in waiters:
                self.results.put((gen, row_id, path, items, next_path, error))
        threading.Thread(target=worker, daemon=True).start()

    def tick(self):
        self.budget.sample()
        self.prop('PerformanceProfile', self.budget.name)
        self.artwork.max_jobs = self.budget.limits['artwork_jobs']
        self.artwork.max_bytes = self.budget.limits['artwork_mb'] * 1024 * 1024
        self.artwork.paused = self.paused or self.budget.pressure
        if self.paused:
            return
        focused_row = self.prop('Row')
        if focused_row.isdigit():
            cid = int(focused_row)
            self.bind_limit = max(self.bind_limit, min(647, cid + 3))
            for upcoming in list(self.pending_bindings):
                if upcoming <= self.bind_limit:
                    self.prop('RowURL' + str(upcoming), self.pending_bindings.pop(upcoming))
            state = self.rows.get(cid)
            if state and not state['loaded'] and not state['busy']:
                self.load(cid, state['source'])
        for cid, state in self.rows.items():
            if not self.budget.pressure and state.get('eager') and not state['loaded'] and not state['busy']:
                self.load(cid, state['source'])
        for cid, (count, position) in list(self.selection.items()):
            if xbmc.getInfoLabel('Container(%s).NumItems' % cid) == str(count):
                self.win.getControl(cid).selectItem(position)
                self.selection.pop(cid)
        request = self.prop('WidgetRequest')
        if request:
            self.prop('WidgetRequest', '')
            data = json.loads(request)
            if data['generation'] == str(self.generation):
                state = self.rows.get(data['row'])
                if state:
                    self.navigate_page(data['row'], back=data.get('back', False),
                                       retry=data.get('retry', False))
        while not self.results.empty():
            generation, cid, path, items, next_path, error = self.results.get_nowait()
            self.prepare_artwork(items)
            ttl = widget_cache_ttl(path)
            if path in self.warm_plan:
                self.warm_active.discard(path)
                if error:
                    self.warm_errors.add(path)
                    self.warm_completed.discard(path)
                else:
                    self.warm_errors.discard(path)
                    self.warm_completed.add(path)
            if not error and ttl and (self.cache.get(path, {}).get('items') != items
                    or time.time()-self.cache.get(path, {}).get('time', 0) >= ttl):
                self.cache[path] = {'time': time.time(), 'items': items, 'next': next_path}
                self.spotlight_dirty = True
                self.trim_cache()
                self.cache_dirty = True
            if generation == -1:
                elapsed = max(0.0, time.monotonic() - self.warm_started.pop(path, time.monotonic()))
                if error:
                    xbmc.log('[Nebelflix] Background cache warm failed in %.2fs: %s' %
                             (elapsed, error), xbmc.LOGWARNING)
                else:
                    xbmc.log('[Nebelflix] Background cache warm ready in %.2fs: %s items' %
                             (elapsed, len(items)), xbmc.LOGINFO)
                continue
            if generation != self.generation:
                owner = self.generation_hubs.get(generation)
                if owner is not self.active_hub:
                    state = owner['rows'].get(cid) if owner else None
                    if state:
                        state.update(busy=False, loaded=not bool(error),
                                     items=items[:10] if state['top10'] else items,
                                     next='' if state['top10'] else next_path)
                    continue
            state = self.rows[cid]
            state['busy'] = False
            if state.get('needs_history_refresh') and path.startswith(('nf://continue', 'nf://mylist')):
                # Discard a stale in-flight result after a manual history edit.
                # The controller's pending refresh will fetch the new Hybrid rows.
                state['loaded'] = False
                continue
            state['loaded'] = True
            elapsed = max(0.0, time.monotonic() - state.pop('request_started', time.monotonic()))
            if error:
                xbmc.log('[Nebelflix] Widget ' + str(cid) + ': ' + error, xbmc.LOGWARNING)
                self.prop('RowState' + str(cid), 'Quelle nicht erreichbar · Erneut versuchen')
                cached = self.cache.get(path)
                if cached and not state['items']:
                    state['items'] = list(cached['items'][:10] if state['top10'] else cached['items'])
            else:
                if state['top10']:
                    items, next_path = items[:10], ''
                if (self.page == 'home'
                        and not state['source'].startswith(('nf://continue', 'nf://addons/', 'nf://genre/'))):
                    items = list(items)
                    random.shuffle(items)
                unique, known = [], set()
                for item in items:
                    key = identity(item)
                    if key not in known:
                        unique.append(item)
                        known.add(key)
                state['items'] = unique
                state['visited'].add(path)
                state['next'] = (next_path if next_path != path else '')
                self.prop('RowState' + str(cid), '' if state['items'] else 'Noch keine Inhalte')
            control = self.win.getControl(cid)
            position = 0 if state['history'] else max(0, control.getSelectedPosition())
            self.publish(cid, self.page_display(cid, error=bool(error)), position)
            xbmc.log('[Nebelflix] Widget %s ready in %.2fs: %s items, next=%s' %
                     (cid, elapsed, len(state['items']), bool(state['next'])), xbmc.LOGINFO)
        if self.cache_dirty and time.monotonic() - self.cache_write_at >= 15:
            self.flush_cache()
        self.artwork.tick()
        self.refresh_logos()
        self.publish_spotlight()
        self.finish_hub_switch()
        self._warm_tick()

    def _warm_tick(self):
        """Fill the disk cache in the background without changing Kodi focus."""
        if (self.paused or self.budget.pressure or time.monotonic() < self.next_warm_at
                or xbmcgui.getCurrentWindowId() != 10000 or xbmc.getCondVisibility('Player.HasMedia')):
            return
        if any(state.get('busy') for state in self.rows.values()):
            return
        # Cap concurrent RPC/plugin directory work to avoid hammering Kodi/iOS.
        while len(self.warm_active) < 1 and self.warm_paths:
            if not self.idle():
                return
            allowed = self.hub_paths.get(self.page, set())
            if self.prop('PrepareWidgets') != 'true':
                eligible = next((i for i, path in enumerate(self.warm_paths) if path in allowed), None)
                if eligible is None:
                    return
                path = self.warm_paths.pop(eligible)
            else:
                path = self.warm_paths.pop(0)
            if path in self.warm_completed:
                continue
            cached = self.cache.get(path)
            ttl = widget_cache_ttl(path)
            if cached and ttl and time.time() - cached.get('time', 0) < ttl:
                self.warm_completed.add(path)
                continue
            with self.lock:
                # Foreground request takes priority; its response is counted
                # as warm completion in tick().
                if path in self.inflight:
                    continue
                self.inflight[path] = [(-1, -1)]
            self.warm_active.add(path)
            self.warm_started[path] = time.monotonic()
            self.next_warm_at = time.monotonic() + self.budget.limits['warm_delay']

            def worker(warm_path=path):
                try:
                    items, next_path = directory(self.rpc, warm_path)
                    error = ''
                except Exception as exc:
                    items, next_path, error = [], '', str(exc)
                with self.lock:
                    waiters = self.inflight.pop(warm_path, [])
                for generation, row_id in waiters:
                    self.results.put((generation, row_id, warm_path, items, next_path, error))

            threading.Thread(target=worker, daemon=True).start()


def _addon_catalog(rpc, kinds=('video', 'music')):
    kodi_types = {'video': 'xbmc.addon.video', 'music': 'xbmc.addon.audio'}
    catalog = {}
    for kind in kinds:
        if kind not in kodi_types:
            continue
        result = rpc('Addons.GetAddons', {'type': kodi_types[kind], 'enabled': True,
            'properties': ['name']}).get('addons') or []
        for addon in result:
            addon_id = str(addon.get('addonid') or '')
            if addon_id and addon_id != 'skin.nebelflix':
                catalog[addon_id] = {'id': addon_id,
                    'name': addon.get('name') or addon_id, 'type': kind}
    return catalog


def _addon_launcher_path(ids, preferred='all'):
    ids = list(dict.fromkeys(str(addon_id) for addon_id in ids if addon_id))
    return 'nf://addons/%s?%s' % (preferred, urlencode({'ids': ','.join(ids)}))


def _select_addon_kind(dialog, heading):
    index = dialog.select(heading, ['Video-Add-ons', 'Musik-Add-ons', 'Video + Musik-Add-ons'])
    return {0: ('video',), 1: ('music',), 2: ('video', 'music')}.get(index, ())


def _addon_selection(dialog, catalog, heading, excluded=()):
    available = [item for addon_id, item in sorted(catalog.items(), key=lambda pair: pair[1]['name'].casefold())
                 if addon_id not in set(excluded)]
    if not available:
        dialog.ok('Nebelflix', 'Keine weiteren passenden Add-ons verfügbar.')
        return []
    selected = dialog.multiselect(heading, [item['name'] for item in available])
    if selected is None:
        return []
    return [available[index]['id'] for index in selected if 0 <= index < len(available)]


def _manage_addon_launcher(rpc, dialog, current_path=''):
    current_ids = []
    catalog = {}
    if current_path:
        parsed = urlparse(current_path)
        current_ids = list(dict.fromkeys(
            value for value in (parse_qs(parsed.query).get('ids') or [''])[0].split(',') if value))
    if current_ids:
        catalog = _addon_catalog(rpc)
        names = [catalog.get(addon_id, {'name': addon_id})['name']
                 for addon_id in current_ids]
        dialog.ok('Add-ons in diesem Widget', '\n'.join(names))
        action = dialog.select('Add-on-Starter verwalten', [
            'Add-on(s) hinzufügen', 'Add-on(s) entfernen', 'Auswahl ersetzen'])
        if action < 0:
            return ''
    else:
        action = 0

    if action == 1:
        installed = [dict(catalog.get(addon_id,
                    {'id': addon_id, 'name': addon_id, 'type': 'unknown'}))
                     for addon_id in current_ids]
        installed.sort(key=lambda item: item['name'].casefold())
        remove = dialog.multiselect('Ausgewählte Add-ons zum Entfernen markieren',
                                    [item['name'] for item in installed])
        if remove is None or not remove:
            return current_path
        removed_ids = {installed[index]['id'] for index in remove
                       if 0 <= index < len(installed)}
        remaining = [addon_id for addon_id in current_ids if addon_id not in removed_ids]
        kinds = {catalog[addon_id]['type'] for addon_id in remaining if addon_id in catalog}
        preferred = next(iter(kinds)) if len(kinds) == 1 else 'all'
        return _addon_launcher_path(remaining, preferred)

    existing = [] if action == 2 else current_ids
    kinds = _select_addon_kind(dialog, 'Add-on-Typ auswählen')
    if not kinds:
        return current_path
    if not catalog:
        catalog = _addon_catalog(rpc)
    selectable = {addon_id: item for addon_id, item in catalog.items()
                  if item['type'] in kinds}
    selected = _addon_selection(dialog, selectable,
        'Add-ons als Startkacheln auswählen', excluded=existing)
    if action == 2:
        selected_ids = selected
    else:
        selected_ids = existing + selected
    if not selected_ids and current_path:
        return current_path
    if not selected_ids:
        return ''
    selected_types = {catalog[addon_id]['type'] for addon_id in selected_ids
                      if addon_id in catalog}
    preferred = next(iter(selected_types)) if len(selected_types) == 1 else 'all'
    return _addon_launcher_path(selected_ids, preferred)


def choose_source(rpc, current_path=''):
    dialog = xbmcgui.Dialog()
    if current_path.startswith('nf://addons/'):
        return _manage_addon_launcher(rpc, dialog, current_path)
    roots = [('Add-on-Kacheln (Video + Musik)', 'addon-launchers'),
             ('Video-Add-on-Inhalte (Bereich auswählen)', 'addons'), ('TV-Sender und Sendergruppen', 'pvr://channels/tv/'),
             ('Nebelflix TV – Vavoo Sendergruppen & EPG', 'nf://nebelflix/tv'),
             ('Kodi-Videobibliothek', 'videodb://'), ('Meine Liste', 'nf://mylist'),
             ('Weiterschauen', 'nf://continue/all'), ('Pfad selbst eingeben', 'manual')]
    choice = dialog.select('Widget-Quelle auswählen', [x[0] for x in roots])
    if choice < 0:
        return ''
    path = roots[choice][1]
    if path == 'manual':
        return dialog.input('Add-on- oder Bibliothekspfad', type=xbmcgui.INPUT_ALPHANUM).strip()
    if path.startswith('nf://'):
        return path
    if path == 'addon-launchers':
        return _manage_addon_launcher(rpc, dialog)
    if path == 'addons':
        addons = rpc('Addons.GetAddons', {'type': 'xbmc.addon.video', 'enabled': True, 'properties': ['name']}).get('addons') or []
        addons = [x for x in addons if x['addonid'] != 'skin.nebelflix']
        index = dialog.select('Add-on auswählen', [x['name'] for x in addons])
        if index < 0:
            return ''
        path = 'plugin://' + addons[index]['addonid'] + '/'
    stack = []
    while True:
        try:
            files = rpc('Files.GetDirectory', {'directory': path, 'media': 'files'}).get('files') or []
        except Exception:
            dialog.ok('Quelle nicht erreichbar', 'Das Add-on oder die TV-Quelle konnte nicht geöffnet werden.')
            return ''
        folders = [x for x in files if x.get('filetype') == 'directory' and x.get('label') != '..']
        index = dialog.select('Bereich als Widget auswählen', ['✓ Diesen Bereich verwenden', '← Zurück'] + [x['label'] for x in folders])
        if index < 0:
            return ''
        if index == 0:
            return path
        if index == 1:
            if not stack:
                return ''
            path = stack.pop()
        else:
            stack.append(path)
            path = folders[index-2]['file']


def edit_layout(rpc, prop):
    dialog = xbmcgui.Dialog()
    config = layout()
    def save():
        write('layout.json', config)
        prop('LayoutChanged', str(time.time_ns()))
    while True:
        tabs = config['tabs']
        choice = dialog.select('Reiter & Widgets verwalten', [x['label'] for x in tabs] + ['+ Reiter hinzufügen'])
        if choice < 0:
            return
        if choice == len(tabs):
            if len(tabs) >= MAX_TABS:
                dialog.ok('Nebelflix', 'Bis zu 12 Reiter sind möglich.'); continue
            name = dialog.input('Name des neuen Reiters').strip()
            if name:
                tabs.append({'id': 'custom_' + uuid.uuid4().hex[:8], 'label': name, 'rows': []}); save()
            continue
        tab = tabs[choice]
        action = dialog.select(tab['label'], ['Umbenennen', 'Widgets bearbeiten', 'Nach links verschieben', 'Nach rechts verschieben', 'Reiter entfernen'])
        if action == 0:
            name = dialog.input('Name des Reiters', defaultt=tab['label']).strip()
            if name:
                tab['label'] = name; save()
        elif action in (2, 3):
            target = choice + (-1 if action == 2 else 1)
            if 0 <= target < len(tabs):
                tabs[choice], tabs[target] = tabs[target], tabs[choice]; save()
        elif action == 4:
            if len(tabs) > 1 and dialog.yesno('Reiter entfernen', 'Nur diesen Reiter und seine Widget-Zuordnungen entfernen?'):
                tabs.pop(choice); save()
        elif action == 1:
            while True:
                rows = tab['rows']
                ri = dialog.select(tab['label'] + ' · Widgets', [x['label'] for x in rows] + ['+ Widget hinzufügen'])
                if ri < 0:
                    break
                if ri == len(rows):
                    if len(rows) >= MAX_ROWS:
                        dialog.ok('Nebelflix', 'Bis zu 48 Reihen pro Reiter sind möglich.'); continue
                    name = dialog.input('Überschrift der Reihe').strip()
                    if name:
                        path = choose_source(rpc)
                        if path:
                            rows.append({'label': name, 'path': path}); save()
                    continue
                ra = dialog.select(rows[ri]['label'], ['Umbenennen', 'Quelle auswählen', 'Nach oben', 'Nach unten', 'Entfernen'])
                if ra == 0:
                    name = dialog.input('Überschrift', defaultt=rows[ri]['label']).strip()
                    if name:
                        rows[ri]['label'] = name; save()
                elif ra == 1:
                    path = choose_source(rpc, rows[ri]['path'])
                    if path:
                        rows[ri]['path'] = path; save()
                elif ra in (2, 3):
                    target = ri + (-1 if ra == 2 else 1)
                    if 0 <= target < len(rows):
                        rows[ri], rows[target] = rows[target], rows[ri]; save()
                elif ra == 4 and dialog.yesno('Widget entfernen', 'Diese Reihe aus dem Reiter entfernen?'):
                    rows.pop(ri); save()


class Progress:
    def __init__(self, rpc, player, prop):
        self.rpc, self.player, self.prop = rpc, player, prop
        self.file = ''; self.item = None; self.saved = 0; self.seek = 0; self.removed = False
        self.remaining = float('inf')
        self.end_margin = 600
        self.retry_at = 0
        self.seek_attempts = 0
        self.seek_retry_at = 0
        self.seek_deadline = 0

    def clear_overlay(self):
        for key in ('PlaybackTitle', 'PlaybackShowTitle', 'PlaybackPlot', 'PlaybackYear',
                    'PlaybackGenres', 'PlaybackRating', 'PlaybackSeason', 'PlaybackEpisode',
                    'PlaybackDBType', 'PlaybackDuration', 'PlaybackFanart', 'PlaybackPoster',
                    'PlaybackClearlogo'):
            self.prop(key, '')

    def finish_item(self):
        # Kodi can report a close callback on a manual stop in some players.
        # Preserve resumable entries unless playback actually reached the end.
        if self.item and self.remaining <= self.end_margin:
            # Hybrid's player finalizes history; do not edit a second store.
            self.prop('RefreshWidgets', str(time.time_ns()))
            if is_direct_media_source(self.item.get('Path')):
                xbmc.log('[Nebelflix] Finished direct playback removed from continue list: ' + self.item.get('Title', ''), xbmc.LOGINFO)

    def tick(self):
        if not self.player.isPlayingVideo():
            if self.file:
                if self.prop('PlaybackEnded') == 'true':
                    self.finish_item()
                self.file = ''; self.item = None
                pending = json.loads(self.prop('PendingPlay') or '{}')
                if not pending or time.time() - pending.get('RequestedAt', 0) >= 180:
                    self.clear_overlay()
                self.prop('PlaybackEnded', '')
                self.prop('RefreshWidgets', str(time.time_ns()))
            return
        if (self.prop('AutoOwned') == 'true' or self.prop('IntroActive') == 'true'
                or self.prop('PlaybackOwner') == 'manual_preview'):
            return
        path = self.player.getPlayingFile()
        if self.prop('PlaybackOwner') == 'trailer':
            if not self.prop('TrailerFile'):
                self.prop('TrailerFile', path)
            if self.prop('TrailerFile') == path:
                return
            self.prop('PlaybackOwner', 'manual'); self.prop('TrailerFile', '')
        # Nebelflix Hybrid briefly plays its dummy file while it resolves a source.
        # Do not consume PendingPlay or save this placeholder as the title.
        normalized_path = path.lower().replace('\\', '/')
        if normalized_path.split('?', 1)[0].endswith('/dummy.mp4'):
            return
        if path != self.file or not self.item:
            if path != self.file:
                self.retry_at = 0
            if time.monotonic() < self.retry_at:
                return
            self.retry_at = time.monotonic() + 1
            self.file = path
            f = self.rpc('Player.GetItem', {'playerid': 1, 'properties': ['title', 'file', 'art', 'uniqueid', 'plot', 'season', 'episode', 'showtitle', 'year', 'genre', 'rating', 'runtime']}).get('item', {})
            item = playback_item(f, path)
            pending = json.loads(self.prop('PendingPlay') or '{}')
            # Pending snapshots expire; never label an unrelated later video as the requested movie.
            if pending and time.time()-pending.get('RequestedAt', 0) < 180:
                requested_sources = (pending.get('StreamURL'), pending.get('Path'))
                path_source = str(path or '').split('|', 1)[0]
                same_source = any(
                    is_direct_media_source(source)
                    and str(source).split('|', 1)[0] == path_source
                    for source in requested_sources if source)
                same_id = item.get('TMDB') and item['TMDB'] == pending.get('TMDB')
                same_title = item.get('Title', '').casefold() == pending.get('Title', '').casefold()
                same_show = f.get('showtitle', '').casefold() == pending.get('Title', '').casefold() and bool(f.get('showtitle'))
                if same_source or same_id or same_title or same_show or pending.get('Path') == path:
                    resolved = dict(pending)
                    for key in ('Title', 'Plot', 'Year', 'Rating', 'Genres', 'Duration',
                                'Fanart', 'Poster', 'Clearlogo', 'TMDB', 'Type', 'DBType',
                                'ShowTitle', 'Season', 'Episode'):
                        value = item.get(key)
                        current = resolved.get(key)
                        if (not current or str(current).casefold() in
                                ('nicht verfügbar', 'not available', 'unknown', 'n/a', 'none')) and value:
                            if str(value).casefold() not in ('nicht verfügbar', 'not available', 'unknown', 'n/a', 'none'):
                                resolved[key] = value
                    if item.get('DBType') == 'episode':
                        for key in ('Type', 'DBType', 'TMDB', 'Season', 'Episode', 'ShowTitle'):
                            if item.get(key) not in (None, ''):
                                resolved[key] = item[key]
                        resolved['Path'] = 'plugin://plugin.video.nebelflix.hybrid/?' + urlencode({
                            'info': 'play', 'tmdb_type': 'tv', 'tmdb_id': resolved.get('TMDB', ''),
                            'season': resolved.get('Season', ''), 'episode': resolved.get('Episode', '')})
                    item = resolved
                    self.prop('PendingPlay', '')
            elif pending:
                # Keep the selected metadata through Nebelflix Hybrid/XShip resolver
                # dummy items; consume it only when its real title begins.
                self.prop('PendingPlay', '')
            if item.get('DBType') not in ('movie', 'episode') and not item.get('TMDB'):
                self.item = None
                self.clear_overlay()
                return
            self.item = item
            xbmc.log('[Nebelflix] Tracking playback: %s [%s:%s S%sE%s]' % (
                item.get('Title', ''), item.get('Type', ''), item.get('TMDB', ''),
                item.get('Season', ''), item.get('Episode', '')), xbmc.LOGINFO)
            self.prop('PlaybackTitle', item.get('Title', ''))
            self.prop('PlaybackShowTitle', item.get('ShowTitle', ''))
            self.prop('PlaybackPlot', item.get('Plot', ''))
            self.prop('PlaybackYear', item.get('Year', ''))
            self.prop('PlaybackGenres', item.get('Genres', ''))
            rating = item.get('Rating', '')
            try:
                rating = '%.1f' % float(rating) if rating else ''
            except (TypeError, ValueError):
                rating = str(rating)
            self.prop('PlaybackRating', rating)
            self.prop('PlaybackSeason', item.get('Season', ''))
            self.prop('PlaybackEpisode', item.get('Episode', ''))
            self.prop('PlaybackDBType', item.get('DBType', ''))
            self.prop('PlaybackDuration', item.get('Duration', ''))
            self.prop('PlaybackFanart', item.get('Fanart', ''))
            self.prop('PlaybackPoster', item.get('Poster', ''))
            self.prop('PlaybackClearlogo', item.get('Clearlogo', ''))
            self.seek = float(item.get('Resume') or 0)
            self.seek_attempts = 0
            self.seek_retry_at = 0
            self.seek_deadline = time.monotonic() + 30 if self.seek > 0 else 0
            self.saved = 0
            self.removed = False
        if not self.item:
            return
        total = self.player.getTotalTime()
        position = self.player.getTime()
        if total > 0:
            self.remaining = total - position
            self.end_margin = min(120, total * 0.05) if self.item.get('DBType') == 'episode' else 600
        if self.seek:
            now = time.monotonic()
            if position >= max(0, self.seek - 5):
                xbmc.log('[Nebelflix] Resume position reached at %.1fs' % position, xbmc.LOGINFO)
                self.seek = 0
            elif now >= self.seek_deadline:
                xbmc.log('[Nebelflix] Resume seek not accepted by source after %d attempts' % self.seek_attempts, xbmc.LOGWARNING)
                self.seek = 0
            elif (total > self.seek + 2 or
                  (is_direct_media_source(path) and position >= 2)) and now >= self.seek_retry_at:
                if self.seek_attempts >= 5:
                    xbmc.log('[Nebelflix] Resume seek not confirmed by source after %d attempts' % self.seek_attempts, xbmc.LOGWARNING)
                    self.seek = 0
                else:
                    try:
                        self.player.seekTime(self.seek)
                        self.seek_attempts += 1
                        self.seek_retry_at = now + 2
                        xbmc.log('[Nebelflix] Seeking to saved position %.1fs (attempt %d)' %
                                 (self.seek, self.seek_attempts), xbmc.LOGINFO)
                        return
                    except Exception as exc:
                        self.seek_attempts += 1
                        self.seek_retry_at = now + 2
                        xbmc.log('[Nebelflix] Resume seek attempt failed: ' + str(exc), xbmc.LOGWARNING)
                        return
        if total <= 0 or time.monotonic()-self.saved < 5:
            return
        self.saved = time.monotonic()
        # Playback positions are written by the Hybrid player into playcount.db.
        # Keep this monitor for artwork/focus only; never write progress.json.
        if not self.removed and total - position <= 900:
            self.removed = True
            self.prop('RefreshWidgets', str(time.time_ns()))
