"""Bridge for MovieScout's remote *Scout* scraper protocol.

MovieScout modules are loaded into a separate namespace. Their public
get_hosters() results are translated to xShip's source dictionaries.
This does not import MovieScout UI, browser or DRM/debrid backends.

Remote Python scripts are executable code: do not update without trusting
the upstream repository. No remote code is loaded unless user opts in and
manually downloads the module set using the manager.
"""
import os
import re
import json
import time
import hashlib
import requests
import types
import inspect
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from resources.lib import control as xcontrol
from resources.lib.movie_scout import multiquest, log

REPO = 'https://raw.githubusercontent.com/mr-evil1/moviescout-modules/main'
NAME_RE = re.compile(r'^[a-z0-9_-]{1,55}$')
PREFIX = 'MS-' 
CACHE_HOURS = 24
MAX_REMOTE_FILE = 700_000
_loaded = {}
_state_cache = None
_manifest_cache = None

def _root():
    path = os.path.join(xcontrol.addonProfilePath, 'movie_scout')
    os.makedirs(path, exist_ok=True)
    return path

def _modules_dir():
    p = os.path.join(_root(), 'scrapers')
    os.makedirs(p, exist_ok=True)
    return p

def _state_path():
    return os.path.join(_root(), 'state.json')

def _state():
    global _state_cache
    if _state_cache is None:
        try:
            with open(_state_path(), encoding='utf-8') as f:
                d = json.load(f)
                _state_cache = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            _state_cache = {}
    return _state_cache

def _save_state():
    tmp = _state_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(_state(), f, indent=2, ensure_ascii=False)
    os.replace(tmp, _state_path())

def _manifest_path():
    return os.path.join(_root(), 'manifest.json')

def _manifest():
    global _manifest_cache
    if _manifest_cache is None:
        try:
            with open(_manifest_path(), encoding='utf-8') as f:
                raw = json.load(f)
            # Cached manifests from older versions can still list these
            # non-Scout/browse-only engines. They are never downloaded as
            # get_hosters() providers, so do not try to import them.
            _manifest_cache = [s for s in raw.get('scrapers', [])
                               if isinstance(s, dict)
                               and NAME_RE.fullmatch(str(s.get('name', '')))
                               and s.get('name') not in {'evil', 'vavootv', 'livetv'}]
        except (OSError, ValueError, TypeError):
            _manifest_cache = []
    return _manifest_cache

def _is_allowed(content):
    # No LG0/LG1 gating: user has requested all manifest scrapers.
    # Upstream modules not exposing get_hosters() still cannot be used as
    # playback providers by xShip without a separate adapter.
    return True


def _module_path(name):
    if not NAME_RE.fullmatch(str(name)):
        raise ValueError('Ungueltiger Scraper-Name')
    return os.path.join(_modules_dir(), name + '.py')

def _meta_path(name):
    return _module_path(name) + '.meta.json'


def _read_metadata(path):
    try:
        with open(path, encoding='utf-8') as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json_atomic(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _download_one(name):
    """Check the module on every startup, keep last known good file on error.

    HTTPS verification and server ETags help with transit integrity and
    bandwidth, but do NOT turn third-party executable Python into trusted
    or sandboxed code. Downloaded code remains a trust decision.
    """
    dest = _module_path(name)
    meta_path = _meta_path(name)
    previous = _read_metadata(meta_path)
    headers = {'User-Agent': multiquest._DEFAULT_UA}
    if os.path.isfile(dest) and previous.get('etag'):
        headers['If-None-Match'] = previous['etag']
    try:
        response = requests.get(REPO + '/scrapers/' + name + '.py',
                                headers=headers, timeout=(4, 12),
                                allow_redirects=False)
        if response.status_code == 304 and os.path.isfile(dest):
            return True
        response.raise_for_status()
        if response.status_code != 200 or len(response.content) > MAX_REMOTE_FILE:
            return False
        # Reject redirects, HTML/captcha error pages and empty files.
        if not response.content or response.content[:64].lstrip().lower().startswith((b'<!doctype',b'<html')):
            return False
        content = response.content.decode('utf-8-sig')
        compile(content, name + '.py', 'exec')
        checksum = hashlib.sha256(response.content).hexdigest()
        if previous.get('sha256') != checksum or not os.path.isfile(dest):
            tmp = dest + '.tmp'
            try:
                with open(tmp,'w',encoding='utf-8') as f:
                    f.write(content)
                os.replace(tmp, dest)
            finally:
                if os.path.exists(tmp): os.remove(tmp)
            _loaded.pop(name, None)
        _write_json_atomic(meta_path, {'sha256': checksum,
                                       'etag': response.headers.get('ETag',''),
                                       'downloaded': time.time()})
        return True
    except Exception:
        # On transient network failures retain and use the old syntax-valid
        # cached module; never partially replace a working copy.
        try:
            with open(dest, encoding='utf-8') as existing:
                compile(existing.read(), dest, 'exec')
            return True
        except Exception:
            return False


def _download_manifest():
    """Return cached or refreshed list; only exact safe module names."""
    global _manifest_cache
    path = _manifest_path()
    previous = _read_metadata(path + '.meta.json')
    headers = {'User-Agent': multiquest._DEFAULT_UA}
    if os.path.isfile(path) and previous.get('etag'):
        headers['If-None-Match'] = previous['etag']
    try:
        response = requests.get(REPO + '/manifest.json', headers=headers,
                                timeout=(4, 10), allow_redirects=False)
        if response.status_code == 304:
            pass
        else:
            response.raise_for_status()
            if response.status_code != 200 or len(response.content) > 1_000_000:
                raise ValueError('invalid manifest HTTP response')
            raw = response.json()
            if not isinstance(raw, dict) or not isinstance(raw.get('scrapers'), list):
                raise ValueError('invalid manifest structure')
            _write_json_atomic(path, raw)
            _write_json_atomic(path + '.meta.json', {'etag':response.headers.get('ETag','')})
    except Exception:
        if not os.path.isfile(path): return []
    raw = _read_metadata(path)
    valid = []
    seen = set()
    for entry in raw.get('scrapers', []):
        if not isinstance(entry, dict): continue
        name = str(entry.get('name',''))
        if not NAME_RE.fullmatch(name) or name in seen: continue
        # These are MovieScout-specific engines outside the Scout scraper API,
        # not playable get_hosters() sources in the public scraper list.
        if name in {'evil', 'vavootv', 'livetv'}: continue
        seen.add(name)
        valid.append(entry)
    if valid:
        _manifest_cache = valid
    return valid


def update_all():
    """Update entire supported manifest without UI or per-module log lines.

    Return (ready, total). A cached valid copy counts as ready if GitHub is
    temporarily unavailable; first installation needs network connectivity.
    """
    entries = _download_manifest()
    if not entries: return 0, 0
    count = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_download_one, entry['name']) for entry in entries]
        for future in as_completed(futures):
            try:
                if future.result(): count += 1
            except Exception:
                pass
    # Persistent status, even when connection failed; not written to Kodi log.
    try:
        _state()['last_update'] = {
            'ready': count, 'total': len(entries), 'timestamp': time.time(),
            'complete': count == len(entries)}
        _save_state()
    except Exception:
        pass
    return count, len(entries)


def startup_update():
    """Called by the service. Never creates dialogs or notifications."""
    if xcontrol.getSetting('mscout.autoupdate') == 'false':
        return
    # On upgrading the manual beta, enable the common scraper engine once;
    # later user changes remain respected.
    try:
        if not _state().get('autoconfigured'):
            xcontrol.setSetting('mscout.enable', 'true')
            _state()['disabled'] = []
            _state()['autoconfigured'] = True
            _save_state()
    except Exception:
        pass
    count, total = update_all()
    if total > 0 and count == total:
        # Exactly one updater-related Kodi log entry, after all manifest
        # modules have been downloaded or validated from the existing cache.
        import xbmc
        xbmc.log('[xShip Hybrid] Alle Module geladen', xbmc.LOGINFO)
    else:
        import xbmc
        xbmc.log('[MovieScout Hybrid] Startprüfung: %d/%d Module verfügbar' %
                 (count, total), xbmc.LOGWARNING)


def _source_module(name):
    if name in _loaded:
        return _loaded[name]
    try:
        path = _module_path(name)
        with open(path, encoding='utf-8') as f:
            src = f.read()
        # Upstream has explicitly deactivated this module; leave it cached but
        # do not register it for playback (all supported ACTIVE ones are on).
        if re.search(r'(?m)^\s*ACTIVE\s*=\s*False\b', src):
            return None
        # The remote authors expect MovieScout's resources.lib.* namespace.
        # Translate their imports to the isolated compatibility package.
        src = re.sub(r'(?m)^(\s*(?:from|import)\s+)resources\.lib(?=\s|\.)',
                     r'\1resources.lib.movie_scout', src)
        # The installed Hybrid already provides its own captcha helpers.
        # Do not redirect this dependency to movie_scout.captcha, which
        # does not exist. Other namespaced MovieScout imports stay isolated.
        src = src.replace('resources.lib.movie_scout.captcha.',
                          'resources.lib.captcha.')
        # Only adapt *local* Kodi paths / xbmcaddon lookups.  A global
        # replacement can accidentally modify literal strings in HTTP headers,
        # POST bodies or URL parameters, changing a provider's original request.
        src = src.replace('special://profile/addon_data/plugin.video.moviescout',
                          'special://profile/addon_data/plugin.video.nebelflix.hybrid/movie_scout')
        for original, replacement in (
            ("xbmcaddon.Addon('plugin.video.moviescout')", "xbmcaddon.Addon('plugin.video.nebelflix.hybrid')"),
            ('xbmcaddon.Addon("plugin.video.moviescout")', 'xbmcaddon.Addon("plugin.video.nebelflix.hybrid")'),
        ):
            src = src.replace(original, replacement)
        module = types.ModuleType('xship_moviescout_' + name)
        module.__file__ = path
        module.__package__ = 'resources.lib.movie_scout'
        exec(compile(src, path, 'exec'), module.__dict__)
        # SITE_DOMAIN is used by many modules dynamically; some may hardcode
        # URLs elsewhere, which cannot be fixed by a domain setting alone.
        override = _state().get('domains', {}).get(name, '')
        if override and hasattr(module, 'SITE_DOMAIN'):
            module.SITE_DOMAIN = override
        fn = getattr(module, 'get_hosters', None)
        if not callable(fn):
            return None
        try:
            params = inspect.signature(fn).parameters
            # MovieScout also ships browse-only scrapers that need a selected
            # page URL. They cannot use the common title/TMDb Scout lookup.
            if 'title' not in params and not any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
                return None
        except (ValueError, TypeError):
            return None
        _loaded[name] = module
        return module
    except Exception:
        log.error()
        return None

class MovieScoutProvider:
    def __init__(self, name, module):
        self.name = name
        self.module = module
        self.priority = 125
        self.tmdb_id = ''

    def resolve(self, url):
        return url  # xShip's ordinary ResolveURL handles page links.

    def run(self, titles, year, season=0, episode=0, imdb=''):
        found = []
        seen = set()
        # Same three title variants as MovieScout's _query_scraper().
        for title in list(dict.fromkeys(str(t) for t in titles if t))[:3]:
            try:
                result = self.module.get_hosters(title=title, year=year or '',
                    season=int(season or 0), episode=int(episode or 0),
                    imdb=imdb or '', tmdb=self.tmdb_id or '')
                for h in result or []:
                    if not isinstance(h, (list, tuple)) or len(h) < 2:
                        continue
                    label, url = str(h[0]), str(h[1])
                    if not url.startswith(('https://','http://','plugin://')):
                        continue
                    host_key = urlparse(url).netloc.lower() if url.startswith('http') else url
                    raw_quality = str(h[3] if len(h) > 3 else 'HD').upper()
                    drm = h[2] if len(h) > 2 else False
                    # DRM license requests need MovieScout's own player;
                    # never feed them into xShip's generic ResolveURL.
                    if isinstance(drm, dict):
                        # A dictionary is not automatically DRM: some providers
                        # attach {'manifest_type': 'hls'} to an open HLS URL.
                        manifest = str(drm.get('manifest_type', '')).lower()
                        if (drm.get('license_key') or drm.get('drm_license')
                                or drm.get('license_type') or manifest in ('mpd', 'dash')):
                            continue  # Widevine/DASH isn't handled by xShip player.
                        is_direct = True
                    else:
                        is_direct = bool(drm)
                    quality = raw_quality
                    if any(q in quality for q in ('2160', '4K', 'UHD')): quality = '4K'
                    elif '1440' in quality or '2K' in quality: quality = '1440p'
                    elif '1080' in quality or 'FHD' in quality: quality = '1080p'
                    elif '720' in quality or quality == 'HD': quality = '720p'
                    else: quality = 'SD'
                    key = (label.lower().strip(), host_key, quality)
                    if key in seen:
                        continue
                    seen.add(key)
                    lang = str(h[4] if len(h) > 4 else '').lower().strip()
                    if lang in ('en','eng','english'):
                        continue  # xShip is currently a German-language catalog.
                    # MovieScout accepts blank language metadata; xShip's German
                    # source filter requires a known value, so label unspecified.
                    language = 'de' if not lang or lang in ('de','ger','deutsch','german') else lang
                    host = label.split('|')[-1].strip() if '|' in label else label
                    if len(host) > 90:
                        host = (urlparse(url).hostname or label)[:90]
                    found.append({'source': host,
                                  'quality': quality, 'language': language,
                                  'url': url, 'direct': is_direct,
                                  'info': 'MovieScout',
                                  'priority': self.priority, 'prioHoster': 100})
            except Exception:
                log.error()
        return found

def get_providers():
    if xcontrol.getSetting('mscout.enable') != 'true':
        return []
    disabled = set(_state().get('disabled', []))
    result = []
    for entry in _manifest():
        name = entry['name']
        if name in disabled:
            continue
        mod = _source_module(name)
        if mod is not None:
            result.append((PREFIX + name, MovieScoutProvider(name, mod)))
    import xbmc
    xbmc.log('[MovieScout Hybrid] %d Suchanbieter registriert (%d Manifest-Einträge)' %
             (len(result), len(_manifest())), xbmc.LOGINFO)
    return result

def manager():
    import xbmcgui
    d = xbmcgui.Dialog()
    items = [
        'Alle Remote-Scraper jetzt aktualisieren',
        'Einzelne Remote-Scraper ein-/ausschalten',
        'Scraper-Domain manuell anpassen',
        'Zusätzliche LG1-Module erlauben: %s' % ('JA' if _state().get('allow_lg1') else 'NEIN'),
        'Hinweis zu Auto-Updates und URL-Aenderungen',
    ]
    index = d.select('MovieScout-Engine (Hybrid)', items)
    if index == 0:
        if not d.yesno('MovieScout-Engine',
            'Python-Scraper werden aus einem fremden GitHub-Repository geladen und ausgefuehrt. '
            'Nur fortfahren, wenn du den Herausgeber vertraust. Jetzt aktualisieren?'):
            return
        n, all_count = update_all()
        d.ok('MovieScout-Engine', '%s von %s Modulen geladen. In den Hybrid-Einstellungen die MovieScout-Engine aktivieren. '
             'LG1-Module bleiben standardmaessig gesperrt.' % (n, all_count))
    elif index == 1:
        entries = [e['name'] for e in _manifest() if os.path.isfile(_module_path(e['name']))]
        if not entries:
            d.ok('MovieScout', 'Noch keine lokal gespeicherten Module. Bitte erst aktualisieren.')
            return
        disabled = set(_state().get('disabled', []))
        lines = [('[AUS] ' if n in disabled else '[AN] ') + n for n in entries]
        selected = d.select('Scraper waehlen (umstellen)', lines)
        if selected >= 0:
            n = entries[selected]
            if n in disabled: disabled.remove(n)
            else: disabled.add(n)
            _state()['disabled'] = sorted(disabled)
            _save_state()
    elif index == 2:
        entries = [e['name'] for e in _manifest()]
        if not entries:
            d.ok('MovieScout', 'Noch kein Manifest geladen.')
            return
        selected = d.select('Scraper waehlen', entries)
        if selected >= 0:
            name = entries[selected]
            old = _state().get('domains', {}).get(name, '')
            value = d.input('Neue Domain ohne https:// (leer = Standard)', defaultt=old).strip().lower()
            if value and not re.fullmatch(r'[a-z0-9.-]{3,253}', value):
                d.ok('MovieScout', 'Ungueltiger Domainname.')
                return
            state = _state().setdefault('domains', {})
            if value: state[name] = value
            else: state.pop(name, None)
            _save_state()
            _loaded.pop(name, None)
    elif index == 3:
        d.ok('MovieScout-Engine',
             'Auto-Update prueft bei jedem Kodi-Start still alle Module aus dem festen Repository. '
             'Ein Domainwechsel kann manuell korrigiert werden; geaenderte Seitenstrukturen '
             'benoetigen neue Scraper. Externer Python-Code wird nicht sandboxed.')
