"""Fullscreen progress for Hybrid source search.

Show a usable logo on the very first frame, use a locally cached TMDb title
logo when available, and never call Kodi GUI controls from network threads.
"""
import json
import os
import threading
import time
from urllib.parse import urlencode

import xbmcgui
import xbmcvfs
from resources.lib import control

_LOGOS = {}
_CACHE_LOADED = False
_CACHE_LOCK = threading.RLock()
_CACHE_MAX_AGE = 30 * 86400


def _cache_file():
    return os.path.join(xbmcvfs.translatePath(control.addonInfo('profile')),
                        'title_logo_cache.json')


def _lookup_logo(media, tmdb_id):
    global _CACHE_LOADED
    if not tmdb_id or not tmdb_id.isdigit():
        return ''
    key = '%s:%s' % (media, tmdb_id)
    with _CACHE_LOCK:
        if not _CACHE_LOADED:
            try:
                with open(_cache_file(), encoding='utf-8') as handle:
                    stored = json.load(handle)
                if isinstance(stored, dict):
                    _LOGOS.update(stored)
            except (OSError, ValueError, TypeError):
                pass
            _CACHE_LOADED = True
        record = _LOGOS.get(key) or {}
        try:
            if (isinstance(record, dict) and
                    str(record.get('url', '')).startswith('https://image.tmdb.org/t/p/') and
                    0 < time.time() - float(record.get('time', 0)) < _CACHE_MAX_AGE):
                return record['url']
        except (TypeError, ValueError):
            pass
    return ''


def _save_logo(media, tmdb_id, url):
    if not (url.startswith('https://image.tmdb.org/t/p/') and tmdb_id.isdigit()):
        return
    with _CACHE_LOCK:
        _LOGOS['%s:%s' % (media, tmdb_id)] = {'url': url, 'time': time.time()}
        try:
            filename = _cache_file()
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            # Keep last 250 entries so storage stays small on iPhone.
            recent = sorted(_LOGOS.items(), key=lambda kv: kv[1].get('time', 0),
                            reverse=True)[:250]
            tmp = filename + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(dict(recent), handle, ensure_ascii=False)
            os.replace(tmp, filename)
        except (OSError, TypeError, ValueError):
            pass


class SourceProgress(xbmcgui.WindowXMLDialog):
    def __new__(cls, meta):
        return super(SourceProgress, cls).__new__(
            cls, 'SourceProgress.xml', control.addonInfo('path'), 'Default', '1080i')

    def __init__(self, meta):
        self.meta = meta if isinstance(meta, dict) else {}
        self.cancelled = False
        self._media = ('movie' if self.meta.get('mediatype') == 'movie' else 'tv')
        self._tmdb_id = str(self.meta.get('tmdb_id') or '')
        self._title_logo = (str(self.meta.get('clearlogo') or '').strip()
                            or _lookup_logo(self._media, self._tmdb_id))
        self._pending_logo = ''
        self._shown_logo = ''
        self._ready = False

    def onInit(self):
        self._ready = True
        self.getControl(10).setImage(self.meta.get('fanart') or
                                     self.meta.get('backdrop_url') or '')
        # No more plain-text-only first screen: show the correct title logo
        # from metadata/cache, or a local Nebelflix clearlogo immediately.
        initial = self._title_logo or self._pending_logo or os.path.join(
            control.addonInfo('path'), 'resources', 'media', 'clearlogo.png')
        self.getControl(11).setImage(initial)
        self._shown_logo = initial
        # Retain the movie title below the Nebelflix placeholder until the
        # real title logo is available; never obscure the bar or artwork.
        self.getControl(12).setLabel('' if (self._title_logo or self._pending_logo)
                                     else str(self.meta.get('title') or ''))

    def create(self, heading, message=''):
        if not self._title_logo and self._tmdb_id.isdigit():
            worker = threading.Thread(target=self.load_logo, daemon=True)
            worker.start()
            # Brief bounded head start. A cache/fast TMDb response now shows
            # the actual title logo on the very first frame. Never wait for a
            # slow API before opening the search UI.
            worker.join(timeout=0.65)
        self.show()
        self.update(0, message)

    def load_logo(self):
        """Only fetch/store metadata here; draw in the UI thread in update()."""
        if self._title_logo or self.cancelled or not self._tmdb_id.isdigit():
            return
        cached = _lookup_logo(self._media, self._tmdb_id)
        if cached:
            self._pending_logo = cached
            return
        try:
            from resources.lib.requestHandler import cRequestHandler
            api_key = control.getSetting('api.tmdb')
            if not api_key:
                return
            query = urlencode({'api_key': api_key,
                               'include_image_language': 'de,en,null'})
            request = cRequestHandler('https://api.themoviedb.org/3/%s/%s/images?%s' %
                                      (self._media, self._tmdb_id, query),
                                      ignoreErrors=True)
            request.cacheTime = 86400
            result = json.loads(request.request())
            logos = result.get('logos') or []
            logos.sort(key=lambda item: (item.get('iso_639_1') not in ('de','en', None),
                                         item.get('iso_639_1') != 'de',
                                         item.get('iso_639_1') != 'en'))
            for logo in logos:
                path = logo.get('file_path') or ''
                if (isinstance(path, str) and path.startswith('/')
                        and '..' not in path and len(path) < 256):
                    url = 'https://image.tmdb.org/t/p/w500' + path
                    _save_logo(self._media, self._tmdb_id, url)
                    self._pending_logo = url
                    break
        except Exception:
            # Search and playback must never fail because of artwork.
            pass

    def close(self):
        self.cancelled = True
        super(SourceProgress, self).close()

    def update(self, percent, message=''):
        if (self._ready and not self.cancelled and self._pending_logo and
                self._shown_logo != self._pending_logo):
            self.getControl(11).setImage(self._pending_logo)
            self.getControl(12).setLabel('')
            self._shown_logo = self._pending_logo
        self.getControl(20).setPercent(max(0, min(100, percent)))
        primary,separator,secondary=str(message or '').partition('[CR]')
        self.getControl(22).setLabel(primary.strip())
        self.getControl(21).setText(secondary.strip() if separator else '')

    def onAction(self, action):
        if action.getId() in (9, 10, 92, 216):
            self.cancelled = True
            self.close()

    def iscanceled(self):
        return self.cancelled


class ContinuousProgress:
    """One owner keeps the search overlay across search, probes and Autoplay."""
    def __init__(self,meta):
        self.dialog=SourceProgress(meta)
        self.created=False
        self.finished=False
    def create(self,heading,message=''):
        if not self.created:
            self.created=True;self.dialog.create(heading,message)
        else:self.dialog.update(0,message)
    def update(self,percent,message=''):
        if not self.finished:self.dialog.update(percent,message)
    def iscanceled(self):return self.dialog.iscanceled()
    def close(self):
        pass  # Intermediate stages release their reference, not the window.
    def finish(self):
        if not self.finished:
            self.finished=True
            if self.created:self.dialog.close()
