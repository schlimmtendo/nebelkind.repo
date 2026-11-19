#!/usr/bin/env python3
"""Self-contained regression test for staged ZIP upgrades and safe retirement."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

TOOLS = Path(__file__).resolve().parent

def load(file, name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

overrides = load("build_overrides.py", "nebelkind_build_overrides")
retire = load("prune_verified_releases.py", "nebelkind_prune_verified")

with TemporaryDirectory(prefix="nebelkind-publish-test-") as folder:
    base = Path(folder)
    packs = base / "packages"
    addon_id = "plugin.video.nebelflix.hybrid"
    addon_dir = packs / addon_id
    addon_dir.mkdir(parents=True)
    old = addon_dir / (addon_id + "-2026.11.15.6.zip")
    with ZipFile(old, "w", ZIP_DEFLATED) as z:
        z.writestr(addon_id + "/addon.xml",
                   '<addon id="plugin.video.nebelflix.hybrid" version="2026.11.15.6">'
                   '<extension point="xbmc.addon.metadata"><assets>'
                   '<icon>resources/icon.png</icon>'
                   '<fanart>resources/missing.jpg</fanart><screenshot/>'
                   '</assets></extension></addon>')
        z.writestr(addon_id + "/resources/lib/player.py", "old player")
        z.writestr(addon_id + "/resources/icon.png", b"TEST-IMAGE")

    overrides.ROOT = base
    overrides.PACKAGES = packs
    overrides.OVERRIDES = base / "overrides"
    overrides.VERSION_FILE = base / "release_versions.json"
    overrides.VERSION_FILE.write_text("{}")
    patch = overrides.OVERRIDES / addon_id / "resources/lib/player.py"
    patch.parent.mkdir(parents=True)
    patch.write_text("fixed player")

    overrides.main()  # test fix: same version stays available manually
    with ZipFile(old) as z:
        assert z.read(addon_id + "/resources/lib/player.py") == b"fixed player"
        assert ET.fromstring(z.read(addon_id + "/addon.xml")).get("version") == "2026.11.15.6"

    overrides.VERSION_FILE.write_text(json.dumps({addon_id: "2026.11.15.7"}))
    overrides.main()  # release fix: newer version becomes installable
    newest = addon_dir / (addon_id + "-2026.11.15.7.zip")
    with ZipFile(newest) as z:
        assert z.testzip() is None
        assert z.read(addon_id + "/resources/lib/player.py") == b"fixed player"
        assert ET.fromstring(z.read(addon_id + "/addon.xml")).get("version") == "2026.11.15.7"

    # Kodi browses artwork via separate packages/<id>/<asset> URLs.
    # The index must reference only URLs that are actually published.
    artwork = load("build_repository.py", "nebelkind_build_repository")
    artwork.ROOT = base
    artwork.PACKAGES = packs
    with ZipFile(newest) as z:
        addon_meta = ET.fromstring(z.read(addon_id + "/addon.xml"))
    artwork.publish_metadata_artwork({addon_id: ("2026.11.15.7", addon_meta)})
    icon = packs / addon_id / "resources/icon.png"
    assert icon.read_bytes() == b"TEST-IMAGE", "Metadata artwork was not published"
    assets = addon_meta.find("./extension[@point='xbmc.addon.metadata']/assets")
    assert len(assets) == 1 and assets[0].tag == "icon", "Broken artwork metadata was not removed"

    manifest = ET.Element("addons")
    ET.SubElement(manifest, "addon", {"id": addon_id, "version": "2026.11.15.7"})
    (packs / "addons.xml").write_bytes(ET.tostring(manifest))
    (packs / "release.json").write_text(json.dumps({
        "schema": 1, "addons": [{"id": addon_id, "version": "2026.11.15.7"}]
    }))
    retire.ROOT = base
    retire.PACKAGES = packs

    # Corrupt newer archive must block all retirement (no old ZIP loss).
    corrupt = addon_dir / (addon_id + "-2026.11.15.8.zip")
    corrupt.write_bytes(b"not a zip file")
    try:
        retire.main()
    except Exception:
        pass
    else:
        raise AssertionError("Corrupt package was not rejected")
    assert old.exists() and newest.exists(), "Failed publish removed a working ZIP"
    corrupt.unlink()

    retire.main()
    assert not old.exists() and newest.exists(), "Successful publish did not retire old ZIP"
    print("PASS: same-version tests, version bump, failure rollback, old-version retirement")

# The skin joins the same release path without packaging any Kodi profile.
skin = 'skin.nebelflix'
assert skin in overrides.ALLOWED and skin in retire.ALLOWED
builder = load('build_repository.py', 'skin_release_builder')
checker = load('check_repository_consistency.py', 'skin_release_checker')
assert skin in builder.ALLOWED and skin in checker.EXPECTED
archives = list((TOOLS.parent / 'packages' / skin).glob(skin + '-*.zip'))
assert archives, 'Skin release is missing'
archive = max(archives, key=lambda p: builder.version_key(p.stem[len(skin)+1:]))
with ZipFile(archive) as z:
    assert z.testzip() is None
    for name in z.namelist():
        assert name.startswith(skin + '/')
        assert not set(Path(name).parts) & {'userdata', 'addon_data', '__pycache__'}
        assert not name.endswith(('.pyc', '.log', '.bak'))
    xml = ET.fromstring(z.read(skin + '/addon.xml'))
    assert archive.name == skin+'-'+xml.get('version')+'.zip'
    assert not xml.findall("./requires/import[@addon='plugin.video.nebelflix.hybrid']")
    assert 'skin.nebelflix' in z.read(skin+'/resources/revisions_updater.py').decode()
    settings = ET.fromstring(z.read(skin+'/xml/Custom_1190_NebelflixSettings.xml'))
    buttons = settings.findall(".//control[@type='button']")
    diagnostic = next(c for c in buttons if c.get('id') == '1216')
    def rect(c):
        return tuple(int(c.findtext(k)) for k in ('left','top','width','height'))
    x,y,w,h = rect(diagnostic)
    for c in buttons:
        if c is diagnostic: continue
        a,b,cw,ch = rect(c)
        assert x+w <= a or a+cw <= x or y+h <= b or b+ch <= y, 'Diagnosis button overlaps '+c.get('id')
print('PASS: skin package, update integration, no profile data, and settings button layout')
