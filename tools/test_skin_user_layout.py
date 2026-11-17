#!/usr/bin/env python3
"""Verify 0.5.84 matches the owner's saved layout and doesn't erase custom tabs."""
import ast, hashlib, json
from pathlib import Path
from zipfile import ZipFile
ROOT = Path(__file__).resolve().parents[1]
AID = 'skin.nebelflix'
VERSION = '0.5.84'
PROFILE_LAYOUT_SHA = 'a6b69b2a474c061f8f244c8e49b2e9fd2b54370db178b79017ae19f809126f29'
def sha_layout(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,
                                     separators=(',',':')).encode()).hexdigest()
plan=json.loads((ROOT/'release_versions.json').read_text(encoding='utf-8'))
if plan.get(AID) != VERSION:
    raise SystemExit(0)
path=ROOT/'packages'/AID/(AID+'-'+VERSION+'.zip')
with ZipFile(path) as z:
    assert z.testzip() is None
    d=json.loads(z.read(AID+'/resources/default-layout.json'))
    assert sha_layout(d)==PROFILE_LAYOUT_SHA, 'Exact uploaded user layout changed'
    assert [(x['id'],x['label']) for x in d['tabs']]==[
        ('home','Startseite'),('tv','Serien'),('movie','Filme'),
        ('new','Neu und beliebt'),('language','VoD'),('live_tv','LiveTV'),
        ('my','Mein Nebelflix')]
    vod=d['tabs'][4]
    assert len(vod['rows'])==4
    assert [x['label'] for x in vod['rows'][2:]]==['Mediathek','Music']
    library=z.read(AID+'/resources/library.py').decode('utf-8')
    assert '# NEBELFLIX_USER_LAYOUT_VOD_0584' in library
    tree=ast.parse(library)
    func=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='layout')
    ns={'json':json,'SKIN':None}
    class FakeSkin:
        def __truediv__(self,part):
            class FakeData:
                def read_text(self,encoding='utf-8'):
                    return json.dumps(d)
            return FakeData()
    ns['SKIN']=FakeSkin()
    wrote=[]
    source=ast.Module(body=[func],type_ignores=[])
    exec(compile(ast.fix_missing_locations(source),'<layout-test>','exec'),ns)
    old={'tabs':[dict(t) for t in d['tabs']]}
    old['tabs']=[dict(t) for t in d['tabs']]
    old_tab=dict(old['tabs'][4]);old_tab['label']='Sprache';old_tab['rows']=old_tab['rows'][:2]
    old['tabs'][4]=old_tab
    old['tabs']=[old['tabs'][0],old['tabs'][1],old['tabs'][2],old['tabs'][3],
                 old['tabs'][6],old['tabs'][4],old['tabs'][5]]
    ns['read']=lambda name,default:old
    ns['write']=lambda name,item:wrote.append((name,item))
    fixed=ns['layout']()
    assert [t['id'] for t in fixed['tabs']]==[t['id'] for t in d['tabs']]
    assert fixed['tabs'][4]['label']=='VoD'
    assert len(fixed['tabs'][4]['rows'])==4 and len(wrote)==1
    # Don't override layouts with personalized language tab/widgets.
    custom={'tabs':[dict(t) for t in d['tabs']]}
    custom['tabs'][4]=dict(custom['tabs'][4],label='Meine Sprache')
    ns['read']=lambda name,default:custom
    wrote.clear()
    assert ns['layout']()['tabs'][4]['label']=='Meine Sprache'
    assert wrote == [], 'Personalized layouts were overwritten'
    # Installer, performance code, and layout order all remain present.
    assert 'ReloadSkin()' in z.read(AID+'/resources/auto_installer.py').decode()
    assert '# NEBELFLIX_FIRETV_LOGO_IO_V1' in library
print('PASS: exact user layout, protective migration and retained AutoInstaller/FireTV code')
