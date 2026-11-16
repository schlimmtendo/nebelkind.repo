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
    addon_id = "skin.nebelflix"
    addon_dir = packs / addon_id
    addon_dir.mkdir(parents=True)
    old = addon_dir / (addon_id + "-0.5.70.zip")
    with ZipFile(old, "w", ZIP_DEFLATED) as z:
        z.writestr(addon_id + "/addon.xml",
                   '<addon id="skin.nebelflix" version="0.5.70">'
                   '<extension point="xbmc.addon.metadata"><assets>'
                   '<icon>resources/nebelflix-icon.png</icon>'
                   '<fanart>resources/missing.jpg</fanart><screenshot/>'
                   '</assets></extension></addon>')
        z.writestr(addon_id + "/xml/Home.xml", "<home>OLD</home>")
        z.writestr(addon_id + "/resources/nebelflix-icon.png", b"TEST-IMAGE")

    overrides.ROOT = base
    overrides.PACKAGES = packs
    overrides.OVERRIDES = base / "overrides"
    overrides.VERSION_FILE = base / "release_versions.json"
    overrides.VERSION_FILE.write_text("{}")
    patch = overrides.OVERRIDES / addon_id / "xml/Home.xml"
    patch.parent.mkdir(parents=True)
    patch.write_text("<home>FIXED</home>")

    overrides.main()  # test fix: same version stays available manually
    with ZipFile(old) as z:
        assert z.read(addon_id + "/xml/Home.xml") == b"<home>FIXED</home>"
        assert ET.fromstring(z.read(addon_id + "/addon.xml")).get("version") == "0.5.70"

    overrides.VERSION_FILE.write_text(json.dumps({addon_id: "0.5.71"}))
    overrides.main()  # release fix: newer version becomes installable
    newest = addon_dir / (addon_id + "-0.5.71.zip")
    with ZipFile(newest) as z:
        assert z.testzip() is None
        assert z.read(addon_id + "/xml/Home.xml") == b"<home>FIXED</home>"
        assert ET.fromstring(z.read(addon_id + "/addon.xml")).get("version") == "0.5.71"

    # Kodi browses artwork via separate packages/<id>/<asset> URLs.
    # The index must reference only URLs that are actually published.
    artwork = load("build_repository.py", "nebelkind_build_repository")
    artwork.ROOT = base
    artwork.PACKAGES = packs
    with ZipFile(newest) as z:
        addon_meta = ET.fromstring(z.read(addon_id + "/addon.xml"))
    artwork.publish_metadata_artwork({addon_id: ("0.5.71", addon_meta)})
    icon = packs / addon_id / "resources/nebelflix-icon.png"
    assert icon.read_bytes() == b"TEST-IMAGE", "Metadata artwork was not published"
    assets = addon_meta.find("./extension[@point='xbmc.addon.metadata']/assets")
    assert len(assets) == 1 and assets[0].tag == "icon", "Broken artwork metadata was not removed"

    manifest = ET.Element("addons")
    ET.SubElement(manifest, "addon", {"id": addon_id, "version": "0.5.71"})
    (packs / "addons.xml").write_bytes(ET.tostring(manifest))
    (packs / "release.json").write_text(json.dumps({
        "schema": 1, "addons": [{"id": addon_id, "version": "0.5.71"}]
    }))
    retire.ROOT = base
    retire.PACKAGES = packs

    # Corrupt newer archive must block all retirement (no old ZIP loss).
    corrupt = addon_dir / (addon_id + "-0.5.72.zip")
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
