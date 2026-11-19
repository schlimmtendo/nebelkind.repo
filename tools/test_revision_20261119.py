"""Validate actual staged release payloads before publishing the Kodi catalog."""
import ast
import json
from pathlib import Path
import threading
import time
from unittest.mock import Mock
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
plan=json.loads((ROOT/'release_versions.json').read_text(encoding='utf-8'))
payloads={}
for aid in ('plugin.video.nebelflix.hybrid','plugin.video.nebelflix.tv','plugin.video.nebelkindmusic'):
    with ZipFile(ROOT/'packages'/aid/(aid+'-'+plan[aid]+'.zip')) as archive:
        assert archive.testzip() is None
        assert ET.fromstring(archive.read(aid+'/addon.xml')).get('version')==plan[aid]
        payloads[aid]={name:archive.read(name) for name in archive.namelist()
                       if name.endswith(('.py','.xml'))}
        for name,data in payloads[aid].items():
            if name.endswith('.py'):
                compile(data,name,'exec')

hybrid=payloads['plugin.video.nebelflix.hybrid']
source=ET.fromstring(hybrid['plugin.video.nebelflix.hybrid/resources/skins/Default/1080i/script-nebelflix-sources.xml'])
labels={node.findtext('label'):node for node in source.findall('.//control') if node.find('label') is not None}
title=labels['$INFO[Window.Property(nf.title)]']
counts=next(node for label,node in labels.items() if 'nf.counts' in label)
assert int(title.findtext('posy'))+int(title.findtext('height'))<=int(counts.findtext('posy'))
namespace={}
exec(hybrid['plugin.video.nebelflix.hybrid/resources/lib/search_workers.py'],namespace)
pool=namespace['SearchExecutor'](max_workers=1)
entered,release=threading.Event(),threading.Event()
def task():entered.set();release.wait(3)
first=pool.submit(task)
assert entered.wait(1)
not_started=Mock();second=pool.submit(not_started)
started=time.monotonic();pool.shutdown(wait=False)
assert time.monotonic()-started<.5
assert second.cancelled();not_started.assert_not_called()
release.set();first.result(timeout=1);pool.shutdown(wait=True)

tv=payloads['plugin.video.nebelflix.tv']
guide=tv['plugin.video.nebelflix.tv/resources/lib/guide.py'].decode('utf-8')
tree=ast.parse(guide);cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Guide')
click=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='onClick')
play=next(n for n in ast.walk(click) if isinstance(n,ast.If) and ast.unparse(n.test)=='controlId == 100')
assert 'self.close()' not in '\n'.join(ast.unparse(n) for n in play.body)
assert "self.group = 'favorites'" in ast.unparse(click)
assert 'self.context_menu()' in ast.unparse(cls)
assert 'PAGE_SIZE=40' in guide
assert "group == 'favorites'" in tv['plugin.video.nebelflix.tv/resources/lib/channels.py'].decode('utf-8')
assert "'TV-Favorit hinzufügen'" in tv['plugin.video.nebelflix.tv/resources/lib/ui.py'].decode('utf-8')
print('PASS: actual release identities, source header geometry, cancellation, persistent TV guide and favorites')
