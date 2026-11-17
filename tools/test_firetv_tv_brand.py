#!/usr/bin/env python3
"""Regression test for Android background limits and non-Estuary TV imagery."""
import ast, io, json
from pathlib import Path
from zipfile import ZipFile
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
plan=json.loads((ROOT/'release_versions.json').read_text())
if plan.get('skin.nebelflix')!='0.5.82' or plan.get('plugin.video.nebelflix.tv')!='0.2.3':
    raise SystemExit(0)
with ZipFile(ROOT/'packages/skin.nebelflix/skin.nebelflix-0.5.82.zip') as z:
    assert z.testzip() is None
    art=z.read('skin.nebelflix/resources/artwork.py').decode()
    lib=z.read('skin.nebelflix/resources/library.py').decode()
    ast.parse(art); ast.parse(lib)
    assert 'self.max_pending = 18 if self.low_power else 42' in art
    assert 'self.max_per_batch = 5 if self.low_power else 10' in art
    assert 'len(self.jobs) >= self.max_pending' in art
    assert 'self.max_logo_pending = 8 if self.low_power else 24' in lib
    assert 'previous != items or not (ROOT / snapshot_name).is_file()' in lib
    assert "self.prepare_artwork(self.cache.get(path, {}).get('items', []))" not in lib
with ZipFile(ROOT/'packages/plugin.video.nebelflix.tv/plugin.video.nebelflix.tv-0.2.3.zip') as z:
    assert z.testzip() is None
    root='plugin.video.nebelflix.tv/'
    icon=z.read(root+'icon.png')
    missing=z.read(root+'resources/media/channel-placeholder.png')
    assert Image.open(io.BytesIO(icon)).size==(256,256)
    assert Image.open(io.BytesIO(missing)).size==(192,192)
    assert icon!=missing
    for fn in ('guide.py','ui.py','playback.py'):
        src=z.read(root+'resources/lib/'+fn).decode()
        ast.parse(src)
        assert 'channel-placeholder.png' in src
    assert 'ACTION_PARENT_DIR' in z.read(root+'resources/lib/guide.py').decode()
print('PASS: bounded Fire TV artwork, no redundant snapshots, TV icon/fallbacks')
