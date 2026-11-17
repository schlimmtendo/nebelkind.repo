#!/usr/bin/env python3
"""Test Fire TV title-logo cache persistence without importing Kodi."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.5.83'
AID = 'skin.nebelflix'


def test_source(source):
    assert '# NEBELFLIX_FIRETV_LOGO_IO_V1' in source
    tree = ast.parse(source)
    widgets = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == 'Widgets')
    selected = [node for node in widgets.body
                if isinstance(node, ast.FunctionDef) and node.name in
                ('refresh_logos', 'flush_logo_cache', 'flush_cache')]
    assert len(selected) == 3
    clazz = ast.ClassDef(name='OnlyLogoMethods', bases=[], keywords=[], body=selected,
                         decorator_list=[])
    test_ast = ast.fix_missing_locations(ast.Module(body=[clazz], type_ignores=[]))
    writes = []
    now = SimpleNamespace(monotonic=lambda: 50.0, time=lambda: 1000.0)
    scope = {'time': now, 'write': lambda name, data: writes.append((name, data))}
    exec(compile(test_ast, '<unit-test>', 'exec'), scope)
    class Future:
        def done(self): return True
        def result(self): return 'https://example.org/logo.png'
    def example(low_power):
        work = scope['OnlyLogoMethods']()
        work.low_power = low_power
        work.cache_dirty = False
        work.cache_write_at = 0.0
        work.logo_cache_dirty = False
        work.logo_cache_write_at = 0.0
        work.logo_refresh_at = 0.0
        work.logo_jobs = {('movie', '123'): Future()}
        work.logos = {}
        work.logo_cache = {}
        work.cache = {'inactive-page': {'items': [{'Title': 'not visible'}]}}
        visible = [{'Type': 'movie', 'TMDB': '123', 'Title': 'visible'}]
        work.snapshots = {600: visible}
        work.rows = {600: {}}
        touched = []
        work.prepare_artwork = lambda items: touched.append(items)
        work.logo_key = lambda item: (item.get('Type'), item.get('TMDB'))
        work.win = SimpleNamespace(getControl=lambda cid: SimpleNamespace(getSelectedPosition=lambda: 0))
        work.publish = lambda cid, items, pos: touched.append(('published', cid))
        return work, visible, touched

    firetv, visible, touched = example(True)
    firetv.refresh_logos()
    assert firetv.logo_cache_dirty
    assert not firetv.cache_dirty
    assert not writes
    assert touched[0] is visible
    assert not any(x is firetv.cache['inactive-page']['items'] for x in touched)
    assert ('published', 600) in touched
    firetv.flush_logo_cache()
    firetv.flush_logo_cache()
    assert [name for name, _ in writes] == ['title-logo-cache.json']

    writes.clear()
    desktop, _, desktop_touched = example(False)
    desktop.refresh_logos()
    assert desktop.cache_dirty and not desktop.logo_cache_dirty
    assert [name for name, _ in writes] == ['title-logo-cache.json']
    assert desktop_touched[0] is desktop.cache['inactive-page']['items']

    writes.clear()
    firetv.cache_dirty = True
    firetv.logo_cache_dirty = True
    firetv.flush_cache()
    assert [name for name, _ in writes] == ['widget-cache.json', 'title-logo-cache.json']
    firetv.flush_cache()
    assert len(writes) == 2
    print('PASS: Android visible-only logos and batched disk writes; desktop unchanged')


def main():
    plan = json.loads((ROOT / 'release_versions.json').read_text(encoding='utf8'))
    if plan.get(AID) != VERSION:
        print('Fire TV I/O regression not scheduled for this release')
        return
    package = ROOT / 'packages' / AID / (AID + '-' + VERSION + '.zip')
    with ZipFile(package) as z:
        assert z.testzip() is None
        source = z.read(AID + '/resources/library.py').decode('utf-8')
        updater = z.read(AID + '/resources/auto_installer.py').decode('utf-8')
        assert 'ReloadSkin()' in updater
        assert 'executebuiltin("Quit")' not in updater
        test_source(source)


if __name__ == '__main__':
    main()
