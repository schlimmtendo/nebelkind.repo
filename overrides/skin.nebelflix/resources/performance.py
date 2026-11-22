"""Conservative device budgets; no benchmark or expensive device probing."""
import re
import time
from urllib.parse import unquote
import xbmc


PROFILES = {
    'low': dict(requests=1, warm_rows=2, initial_rows=2, artwork_jobs=4,
                artwork_items=4, logos=1, cache_entries=50, warm_delay=2.0,
                poster='w342', fanart='w780', artwork_mb=64),
    'balanced': dict(requests=2, warm_rows=3, initial_rows=3, artwork_jobs=8,
                     artwork_items=8, logos=2, cache_entries=90, warm_delay=0.8,
                     poster='w500', fanart='w1280', artwork_mb=128),
    'desktop': dict(requests=2, warm_rows=4, initial_rows=4, artwork_jobs=12,
                    artwork_items=10, logos=2, cache_entries=120, warm_delay=0.4,
                    poster='w500', fanart='w1280', artwork_mb=256),
}


def memory_mb(value):
    text = str(value or '').strip().lower().replace(',', '.')
    match = re.search(r'(\d+(?:\.\d+)?)', text)
    if not match:
        return None
    number = float(match.group(1))
    if 'gb' in text or 'gib' in text:
        number *= 1024
    elif 'kb' in text or 'kib' in text:
        number /= 1024
    return number if number > 0 else None


def choose(android, total, free, override='auto'):
    if override in PROFILES:
        return override
    if android or (total is not None and total <= 3072) or (free is not None and free < 400):
        return 'low'
    if total is None or total < 6144:
        return 'balanced'
    return 'desktop'


class Budget:
    def __init__(self, override='auto'):
        self.total = memory_mb(xbmc.getInfoLabel('System.Memory(total)'))
        self.free = memory_mb(xbmc.getInfoLabel('System.Memory(free)'))
        self.name = choose(bool(xbmc.getCondVisibility('System.Platform.Android')),
                           self.total, self.free, override)
        self.limits = dict(PROFILES[self.name])
        self.next_sample = 0
        self.pressure = False

    def sample(self):
        if time.monotonic() < self.next_sample:
            return
        self.next_sample = time.monotonic() + 5
        self.free = memory_mb(xbmc.getInfoLabel('System.Memory(free)'))
        if self.free is None:
            return
        # Hysteresis avoids repeatedly starting/stopping work near the threshold.
        self.pressure = self.free < (300 if self.pressure else 180)
        if self.pressure and self.name != 'low':
            self.name = 'low'
            self.limits = dict(PROFILES['low'])
            xbmc.log('[Nebelflix Performance] Speicherdruck: sparsames Profil aktiv', xbmc.LOGWARNING)

    def image(self, url, field, tile=False):
        if not isinstance(url, str):
            return ''
        if url.startswith('image://'):
            # JSON-RPC returns Kodi-wrapped artwork, including in existing caches.
            wrapped = url[len('image://'):]
            decoded = unquote(wrapped[:-1] if wrapped.endswith('/') else wrapped)
            if decoded.startswith(('https://', 'http://')) and '?' not in wrapped:
                url = decoded
        size = self.limits['fanart'] if field == 'Fanart' else self.limits['poster']
        if tile and field == 'Fanart':
            size='w500' if self.name=='low' else 'w780'
        if field == 'Clearlogo':
            if self.name!='low':return url
            size='w300'
        return re.sub(r'^(https?://image\.tmdb\.org/t/p/)(?:original|w\d+)(/)',
                      lambda match: match[1] + size + match[2], url)
