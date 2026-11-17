#!/usr/bin/env python3
"""Reduce Fire TV cache churn when refreshing clearlogos in NebelFlix Skin.

No changes to the installer, widget sources, artwork downloads, or TV add-on.
Only patches skin.nebelflix 0.5.83. Fail closed on unexpected sources.
"""
import json
import os
from pathlib import Path
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.5.83'
AID = 'skin.nebelflix'
MEMBER = AID + '/resources/library.py'
MARK = '# NEBELFLIX_FIRETV_LOGO_IO_V1'


def replace_once(source, old, new, label):
    count = source.count(old)
    if count != 1:
        raise ValueError('%s: expected one code anchor, got %d' % (label, count))
    return source.replace(old, new, 1)


def patch(source):
    if MARK in source:
        return source
    if 'self.max_logo_pending = 8 if self.low_power else 24' not in source:
        raise ValueError('NebelFlix 0.5.82 performance base is missing')
    source = replace_once(source,
        '        self.logo_refresh_at = 0\n',
        '        self.logo_refresh_at = 0\n'
        '        self.logo_cache_dirty = False\n'
        '        self.logo_cache_write_at = time.monotonic()\n',
        'pending cache initialization')
    source = replace_once(source,
        '''    def flush_cache(self):
        if self.cache_dirty:
            write('widget-cache.json', self.cache)
            self.cache_dirty = False
            self.cache_write_at = time.monotonic()
''',
        '''    def flush_cache(self):
        if self.cache_dirty:
            write('widget-cache.json', self.cache)
            self.cache_dirty = False
            self.cache_write_at = time.monotonic()
        # Explicit cache flush also persists any delayed Android logos.
        self.flush_logo_cache()

    def flush_logo_cache(self):
        if self.logo_cache_dirty:
            write('title-logo-cache.json', self.logo_cache)
            self.logo_cache_dirty = False
            self.logo_cache_write_at = time.monotonic()
''',
        'cache flush')
    source = replace_once(source,
        '''        self.logo_cache = dict(sorted(self.logo_cache.items(),
                                     key=lambda entry: entry[1].get('time', 0), reverse=True)[:2000])
        write('title-logo-cache.json', self.logo_cache)
        self.logo_refresh_at = time.monotonic() + 2
        for record in self.cache.values():
            self.prepare_artwork(record.get('items', []))
        self.cache_dirty = True
''',
        '''        self.logo_cache = dict(sorted(self.logo_cache.items(),
                                     key=lambda entry: entry[1].get('time', 0), reverse=True)[:2000])
        # NEBELFLIX_FIRETV_LOGO_IO_V1: refresh only active rows on Android.
        # The title-logo cache is persisted independently from widget data.
        if self.low_power:
            self.logo_cache_dirty = True
        else:
            write('title-logo-cache.json', self.logo_cache)
        self.logo_refresh_at = time.monotonic() + 2
        if self.low_power:
            for items in tuple(self.snapshots.values()):
                self.prepare_artwork(items)
        else:
            for record in self.cache.values():
                self.prepare_artwork(record.get('items', []))
            self.cache_dirty = True
''',
        'logo background refresh')
    source = replace_once(source,
        '''        self.artwork.tick()
        self.refresh_logos()
        self._warm_tick()
''',
        '''        self.artwork.tick()
        self.refresh_logos()
        # Save Android logo batches at most every 15 s unless cache flushes.
        if self.low_power and self.logo_cache_dirty and (
                time.monotonic() - self.logo_cache_write_at >= 15):
            self.flush_logo_cache()
        self._warm_tick()
''',
        'deferred persistence')
    compile(source, MEMBER, 'exec')
    return source


def main():
    plan = json.loads((ROOT / 'release_versions.json').read_text(encoding='utf-8'))
    if plan.get(AID) != VERSION:
        print('Fire TV cache-I/O patch not scheduled, skipping')
        return
    path = ROOT / 'packages' / AID / (AID + '-' + VERSION + '.zip')
    if not path.is_file():
        raise ValueError('Missing target release ZIP: ' + str(path))
    with ZipFile(path) as original:
        old = original.read(MEMBER)
        new = patch(old.decode('utf-8')).encode('utf-8')
        if old == new:
            print('Fire TV logo I/O optimization already installed')
            return
        fd, tmp = tempfile.mkstemp(prefix='neb-firetv-cache-', suffix='.zip', dir=path.parent)
        os.close(fd)
        try:
            with ZipFile(tmp, 'w') as output:
                for item in original.infolist():
                    output.writestr(item, new if item.filename == MEMBER else original.read(item.filename))
            with ZipFile(tmp) as check:
                if check.testzip() is not None:
                    raise ValueError('Patched skin ZIP failed integrity test')
                if MARK.encode('utf-8') not in check.read(MEMBER):
                    raise ValueError('Optimization not present in output')
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    print('Optimized Fire TV title-logo cache I/O in', path.name)


if __name__ == '__main__':
    main()
