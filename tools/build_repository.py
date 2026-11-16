#!/usr/bin/env python3
"""Publish only NebelFlix Skin and three related Nebelkind add-ons.
All uploaded packages are public immediately. Kodi auto-updates require higher versions.
"""
import hashlib
import io
import re
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
PACKAGES=ROOT/'packages'
REPO_ID='repository.nebelkind'
ALLOWED={REPO_ID,'skin.nebelflix','plugin.video.nebelflix.hybrid','plugin.video.nebelflix.tv','plugin.video.nebelkindmusic'}
VERSION=re.compile(r'^[0-9][0-9A-Za-z.+_-]*$')


def generate_branding():
    import cairosvg
    from PIL import Image, ImageFilter, ImageDraw
    p=ROOT/REPO_ID
    p.mkdir(exist_ok=True)
    raw=cairosvg.svg2png(url=str(ROOT/'assets/nebelkind-logo.svg'),output_width=512,output_height=512)
    icon=Image.open(io.BytesIO(raw)).convert('RGB')
    icon.save(p/'icon.png',optimize=True)
    fan=Image.new('RGB',(1280,720),(2,8,22))
    blur=icon.resize((1280,1280)).crop((0,280,1280,1000)).filter(ImageFilter.GaussianBlur(34))
    fan.paste(blur,(0,0))
    layer=Image.new('RGBA',fan.size,(0,0,0,0))
    drawer=ImageDraw.Draw(layer)
    drawer.rectangle((0,0,1280,720),fill=(1,7,20,105))
    fan=Image.alpha_composite(fan.convert('RGBA'),layer)
    logo=icon.resize((500,500),Image.Resampling.LANCZOS).convert('RGBA')
    fan.alpha_composite(logo,(390,110))
    fan.convert('RGB').save(p/'fanart.jpg',quality=88,optimize=True)


def repo_zip():
    addon=ROOT/REPO_ID
    xml=ET.parse(addon/'addon.xml').getroot()
    assert xml.attrib['id']==REPO_ID
    version=xml.attrib['version']
    path=PACKAGES/REPO_ID/f'{REPO_ID}-{version}.zip'
    path.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(path,'w',ZIP_DEFLATED,compresslevel=9) as z:
        for item in sorted(addon.rglob('*')):
            if item.is_file(): z.write(item,item.relative_to(ROOT).as_posix())
    print('Repo installer:',path.relative_to(ROOT))


def version_key(text):
    return tuple((1,int(x)) if x.isdigit() else (0,x.lower()) for x in re.split('[._-]',text))


def validate_zip(path):
    if not path.is_file(): raise ValueError(f'Missing: {path}')
    with ZipFile(path) as z:
        names=z.namelist()
        if not names or any(n.startswith('/') or '..' in Path(n).parts or '\\' in n for n in names):
            raise ValueError(f'Invalid ZIP member in {path}')
        for info in z.infolist():
            if info.file_size>250_000_000: raise ValueError(f'Oversized ZIP entry in {path}')
        xmls=[n for n in names if n.count('/')==1 and n.endswith('/addon.xml')]
        if len(xmls)!=1: raise ValueError(f'Expected exactly one root addon.xml in {path}')
        xml=ET.fromstring(z.read(xmls[0]))
        addon_id,version=xml.get('id'),xml.get('version')
        if addon_id not in ALLOWED or addon_id!=xmls[0].split('/')[0]:
            raise ValueError(f'Unexpected addon id in {path}: {addon_id}')
        if not version or not VERSION.fullmatch(version):
            raise ValueError(f'Invalid addon version: {version}')
        expected=PACKAGES/addon_id/f'{addon_id}-{version}.zip'
        if path!=expected: raise ValueError(f'ZIP path must be {expected}')
        deps = [dep for dep in xml.findall('./requires/import')
                if dep.get('addon') in ('skin.nebelflix', 'plugin.video.nebelflix.hybrid')]
        if addon_id == 'skin.nebelflix':
            if any(dep.get('addon') == 'plugin.video.nebelflix.hybrid'
                   and dep.get('optional') != 'true' for dep in deps):
                raise ValueError('Skin must not require Hybrid (circular dependency)')
        elif addon_id == 'plugin.video.nebelkindmusic':
            if any(dep.get('addon') == 'skin.nebelflix' for dep in deps):
                raise ValueError('Music must be standalone without Skin dependency')
        elif addon_id != REPO_ID:
            matches = [dep for dep in deps if dep.get('addon') == 'skin.nebelflix']
            if (len(matches) != 1 or matches[0].get('optional') == 'true'
                    or not matches[0].get('version')):
                raise ValueError('Addon requires skin.nebelflix: ' + addon_id)
        return addon_id,version,xml


def main():
    generate_branding()
    repo_zip()
    entries={}
    for path in sorted(PACKAGES.glob('*/*.zip')):
        addon_id,version,xml=validate_zip(path)
        if addon_id not in entries or version_key(version)>version_key(entries[addon_id][0]):
            entries[addon_id]=(version,xml)
    node=ET.Element('addons')
    for key in sorted(entries):node.append(entries[key][1])
    ET.indent(node,space='  ')
    data=b'<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(node,encoding='utf-8')+b'\n'
    PACKAGES.mkdir(exist_ok=True)
    (PACKAGES/'addons.xml').write_bytes(data)
    (PACKAGES/'addons.xml.md5').write_text(hashlib.md5(data).hexdigest(),encoding='ascii')
    print('Indexed:',', '.join(f'{k} {v[0]}' for k,v in sorted(entries.items())))


if __name__=='__main__':
    try:main()
    except Exception as e:sys.exit(f'Kodi repository build failed: {e}')
