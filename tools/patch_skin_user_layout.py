#!/usr/bin/env python3
"""Ship the owner's approved NebelFlix 0.5.83 layout as 0.5.84 defaults.

The user's installed 0.5.83 skin ZIP is the source-of-truth for code hashes.
The separate userdata ZIP holds the user layout. Its unrelated files must NEVER
be published: widget cache, personal watch history, and settings are private.
The layout differs only by tab ordering, VoD label and two widgets.

For existing profiles, a narrow in-place migration upgrades ONLY the exact old
stock 'Sprache' tab. All personalized layouts remain untouched.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
AID = 'skin.nebelflix'
VERSION = '0.5.84'
LIB = AID + '/resources/library.py'
DEFAULT = AID + '/resources/default-layout.json'
PROFILE_LAYOUT_SHA = 'a6b69b2a474c061f8f244c8e49b2e9fd2b54370db178b79017ae19f809126f29'
BASE_HASHES = {
    AID + '/resources/library.py': '5358763b115c414c4491bd57cbfdb2e2852fef73720bb6248655ae8e465a50ab',
    AID + '/resources/controller.py': 'cc6dc3be9363aa6f1f5a26c156543d4b49f557a9ee1c03b094b40a607ed69f6e',
    AID + '/resources/auto_installer.py': 'c3231545cc2dfff3f07ba77c349be548a8ed3cd9504ae014e24d8a34dd462983',
    AID + '/xml/Home.xml': '77f3c73837f5b1a193c2b6ea50f54c5c29202050f33932f53dd9edc38fdb3d27',
    AID + '/xml/Includes_Home.xml': '94426de572ce6a842257858be8904e2c928db270d418f43bfe11c900edd179fa',
    AID + '/resources/artwork.py': '652d588f2d6b4acd3425154296d9705955af3faf00e1fca9c80fcb8dea7275ac',
    DEFAULT: 'e02d9f8242316f1d3f0c18b9ae68eeb3afb3415850eec56e1ab2d5e645b3ec05'
}
EXPECTED_ORDER = ['home', 'tv', 'movie', 'new', 'language', 'live_tv', 'my']
MEDIA = {
    'label': 'Mediathek',
    'path': 'nf://addons/video?ids=plugin.video.xship%2Cplugin.video.gezkino%2Cplugin.video.flimmerboxyt%2Cplugin.video.megakino%2Cplugin.video.moviescout%2Cplugin.video.multitrailer%2Cplugin.video.vavoo.tv.plus%2Cplugin.video.xstream'
}
MUSIC = {
    'label': 'Music',
    'path': 'nf://addons/all?ids=plugin.audio.mp3streams%2Cplugin.video.nebelkindmusic%2Cplugin.video.All.Kinds.of.Music%2Cplugin.video.djplaylist'
}
MARK = '# NEBELFLIX_USER_LAYOUT_VOD_0584'


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def patch_default(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or len(data.get('tabs', [])) != 7:
        raise ValueError('Unexpected default layout structure')
    tabs = data['tabs']
    ids = [t.get('id') for t in tabs]
    if set(ids) != set(EXPECTED_ORDER):
        raise ValueError('Unexpected default tabs')
    vod = next(t for t in tabs if t['id'] == 'language')
    if vod.get('label') != 'Sprache' or len(vod.get('rows', [])) != 2:
        raise ValueError('Default VoD layout already personalized: refusing overwrite')
    vod['label'] = 'VoD'
    vod['rows'].extend([MEDIA, MUSIC])
    lookup = {t['id']: t for t in tabs}
    data['tabs'] = [lookup[aid] for aid in EXPECTED_ORDER]
    if fingerprint(data) != PROFILE_LAYOUT_SHA:
        raise ValueError('Generated default layout differs from approved userdata layout')
    return json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'


def patch_library(raw):
    text = raw.decode('utf-8')
    old = """def layout():
    return read('layout.json', json.loads((SKIN / 'resources/default-layout.json').read_text(encoding='utf-8')))
"""
    new = """def layout():
    # NEBELFLIX_USER_LAYOUT_VOD_0584
    # Keep personalized layouts intact. Only migrate the exact old stock tab.
    default = json.loads((SKIN / 'resources/default-layout.json').read_text(encoding='utf-8'))
    data = read('layout.json', default)
    if isinstance(data, dict) and isinstance(data.get('tabs'), list):
        tabs = data['tabs']
        vod = next((t for t in tabs if isinstance(t, dict) and t.get('id') == 'language'), None)
        stock = next((t for t in default['tabs'] if t['id'] == 'language'), None)
        if (vod and stock and vod.get('label') == 'Sprache'
                and vod.get('rows') == stock['rows'][:2]):
            vod['label'] = stock['label']
            vod['rows'] = [dict(row) for row in stock['rows']]
            ids = [t.get('id') for t in tabs if isinstance(t, dict)]
            if ids == ['home', 'tv', 'movie', 'new', 'my', 'language', 'live_tv']:
                order = {t['id']: t for t in tabs}
                data['tabs'] = [order[x] for x in
                                ('home', 'tv', 'movie', 'new', 'language', 'live_tv', 'my')]
            write('layout.json', data)  # Backs up existing userdata layout.
    return data
"""
    if text.count(old) != 1:
        raise ValueError('Skin layout() implementation changed: aborting instead of overwriting')
    text = text.replace(old, new, 1)
    compile(text, LIB, 'exec')
    return text.encode('utf-8')


def main():
    plan = json.loads((ROOT / 'release_versions.json').read_text('utf-8'))
    if plan.get(AID) != VERSION:
        print('User layout release not scheduled, skipping')
        return
    package = ROOT / 'packages' / AID / (AID + '-' + VERSION + '.zip')
    if not package.is_file():
        raise ValueError('Missing staged skin ZIP')
    with ZipFile(package) as archive:
        assert archive.testzip() is None
        for member, expected in BASE_HASHES.items():
            raw = archive.read(member)
            actual = hashlib.sha256(raw).hexdigest()
            if actual != expected:
                raise ValueError('Skin changed vs owner ZIP, refusing release: ' + member)
        new_layout = patch_default(archive.read(DEFAULT).decode('utf-8'))
        new_lib = patch_library(archive.read(LIB))
        fd, temp = tempfile.mkstemp(prefix='neb-userlayout-', suffix='.zip', dir=package.parent)
        os.close(fd)
        try:
            with ZipFile(temp, 'w') as out:
                for info in archive.infolist():
                    content = archive.read(info.filename)
                    if info.filename == DEFAULT:
                        content = new_layout
                    elif info.filename == LIB:
                        content = new_lib
                    out.writestr(info, content)
            with ZipFile(temp) as verify:
                if verify.testzip() is not None:
                    raise ValueError('User layout package corrupted')
                if fingerprint(json.loads(verify.read(DEFAULT))) != PROFILE_LAYOUT_SHA:
                    raise ValueError('Built-in layout mismatch')
                if MARK.encode() not in verify.read(LIB):
                    raise ValueError('User layout migration missing')
            os.replace(temp, package)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    print('Packaged the approved VoD, Mediathek and Music default layout in 0.5.84')


if __name__ == '__main__':
    main()
