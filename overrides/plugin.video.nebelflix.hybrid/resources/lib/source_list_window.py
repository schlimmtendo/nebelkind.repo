# -*- coding: utf-8 -*-
"""Skin-independent, poster-based source picker for Nebelflix Hybrid.

This module only renders and selects an existing scraper result. It does not
import, run or modify any provider or MovieScout scraper.
"""
import json
import re
import time
from resources.lib import load_timing

import xbmcaddon
import xbmcgui
import xbmcvfs
from resources.lib.quality_policy import source_quality

ADDON_ID = 'plugin.video.nebelflix.hybrid'
BACK_ACTIONS = (9, 10, 92, 216, 247)


def _plain(value):
    """Remove Kodi formatting for secondary text, without changing source data."""
    value = re.sub(r'\[(?:/?(?:COLOR|B|I|LIGHT|UPPERCASE|LOWERCASE))(?:\s+[^\]]*)?\]', '', str(value or ''), flags=re.I)
    return re.sub(r'\s+', ' ', value).strip()


def _metadata(meta):
    if isinstance(meta, dict):
        return meta
    try:
        obj = json.loads(meta or '{}')
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        return {}


QUALITY = {
    4: ('4K', '4K', 'FFE997FF'),
    3: ('1440P', '1440p', 'FF71DBEC'),
    2: ('1080P', '1080p', 'FFF6C15A'),
    1: ('720P', '720p', 'FF87B9FF'),
    0: ('SD', 'SD', 'FFC2C9D1'),
   -1: ('SD', 'SD', 'FFC2C9D1'),
}


def source_counts(items):
    counts = {'4K': 0, '1440p': 0, '1080p': 0, '720p': 0, 'SD': 0}
    for item in items:
        _, bucket, _ = QUALITY[source_quality(item)]
        counts[bucket] += 1
    return counts


class HybridSourceList(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self.entries = kwargs.pop('entries', [])
        self.metadata = _metadata(kwargs.pop('metadata', {}))
        self.result_index = -1
        super().__init__(*args, **kwargs)

    def onInit(self):
        meta = self.metadata
        title = _plain(meta.get('title') or meta.get('originaltitle') or 'Quellen auswählen')
        season = meta.get('season')
        episode = meta.get('episode')
        if season not in (None, '', 0, '0') and episode not in (None, ''):
            try:
                title += '  S%02dE%02d' % (int(season), int(episode))
            except (ValueError, TypeError):
                pass
        plot = _plain(meta.get('plot') or meta.get('overview') or 'Keine Beschreibung verfügbar.')
        poster = str(meta.get('cover_url') or meta.get('poster') or '')
        if not poster:
            poster = xbmcaddon.Addon(ADDON_ID).getAddonInfo('icon')
        self.setProperty('nf.title', title)
        self.setProperty('nf.poster', poster)
        self.setProperty('nf.plot', plot)
        self.setProperty('nf.count', '%s Quellen' % len(self.entries))
        counts = source_counts(self.entries)
        summary = '[COLOR FFE997FF]4K %d[/COLOR]' % counts['4K']
        if counts['1440p']:
            summary += '     [COLOR FF71DBEC]1440p %d[/COLOR]' % counts['1440p']
        summary += ('     [COLOR FFF6C15A]1080p %d[/COLOR]'
                    '     [COLOR FF87B9FF]720p %d[/COLOR]'
                    '     [COLOR FFC2C9D1]SD %d[/COLOR]') % (
                        counts['1080p'], counts['720p'], counts['SD'])
        self.setProperty('nf.counts', summary)
        lst = self.getControl(500)
        lst.reset()
        for index, src in enumerate(self.entries):
            quality, quality_group, quality_color = QUALITY[source_quality(src)]
            provider = _plain(src.get('provider') or 'Unbekannt')
            host = _plain(src.get('source') or 'Unbekannt')
            detail = _plain(src.get('info') or '')
            probe = src.get('_probe') if isinstance(src.get('_probe'), dict) else {}
            parts = [provider.upper(), host.upper()]
            if detail:
                parts.append(detail)
            if probe.get('bitrate'):
                parts.append(str(probe['bitrate']))
            row = xbmcgui.ListItem(label='%d. %s  /  %s' % (index + 1, provider.upper(), host.upper()))
            row.setProperty('nf.quality', quality)
            row.setProperty('nf.quality_group', quality_group)
            row.setProperty('nf.detail', '   |   '.join(parts)[:260])
            row.setProperty('nf.language', _plain(src.get('language') or '').upper())
            row.setProperty('nf.number', '%02d' % (index + 1))
            row.setProperty('nf.quality_color', quality_color)
            lst.addItem(row)
        if self.entries:
            lst.selectItem(0)
        self.setFocusId(500)

    def onClick(self, control_id):
        if control_id == 500:
            pos = self.getControl(500).getSelectedPosition()
            if 0 <= pos < len(self.entries):
                self.result_index = pos
            self.close()
        elif control_id == 501:
            self.close()

    def onAction(self, action):
        if action.getId() in BACK_ACTIONS:
            self.close()


def choose_source(items, meta):
    """Return selected index (-1 on cancel); errors bubble to fallback dialog."""
    started=time.monotonic()
    addon_path = xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo('path'))
    dialog = HybridSourceList('script-nebelflix-sources.xml', addon_path,
                              'Default', '1080i', entries=items, metadata=meta)
    try:
        dialog.doModal()
        return dialog.result_index
    finally:
        load_timing.record('hybrid','source.picker',started,count=len(items),status='selected' if dialog.result_index>=0 else 'cancelled')
        del dialog
