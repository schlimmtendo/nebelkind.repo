#!/usr/bin/env python3
"""Fail a Kodi release before publication if metadata, ZIPs or artwork disagree.

All verification happens in the GitHub Actions worktree. If it fails, the
live repository and its previous addon ZIPs stay untouched.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
EXPECTED = {
    "repository.nebelkind",
    "skin.nebelflix",
    "plugin.video.nebelflix.hybrid",
    "plugin.video.nebelflix.tv",
    "plugin.video.nebelkindmusic",
}


def fail(message):
    raise ValueError("Kodi repository consistency check failed: " + message)


def imports(xml):
    return {(d.get("addon"), d.get("version"), d.get("optional", "false"))
            for d in xml.findall("./requires/import")}


def check():
    data = (PACKAGES / "addons.xml").read_bytes()
    checksum = (PACKAGES / "addons.xml.md5").read_text(encoding="ascii").strip()
    if hashlib.md5(data).hexdigest() != checksum:
        fail("addons.xml.md5 does not match addons.xml")
    root = ET.fromstring(data)
    if root.tag != "addons":
        fail("addons.xml must have one <addons> root element")
    items = root.findall("addon")
    addon_ids = [x.get("id") for x in items]
    if set(addon_ids) != EXPECTED or len(addon_ids) != len(EXPECTED):
        fail("Expected exactly the repo installer and four uniquely named addons")
    nodes = dict(zip(addon_ids, items))

    version_manifest = json.loads((PACKAGES / "release.json").read_text(encoding="utf-8"))
    if version_manifest.get("schema") != 1:
        fail("Unsupported release.json schema")
    entries = version_manifest.get("addons")
    if not isinstance(entries, list) or len(entries) != 4:
        fail("Version manifest must contain four addons")
    versions = {entry.get("id"): entry.get("version") for entry in entries}
    if len(versions) != 4:
        fail("Duplicate ID in release.json")
    for addon_id in EXPECTED - {"repository.nebelkind"}:
        if nodes[addon_id].get("version") != versions.get(addon_id):
            fail("release.json version disagrees with Kodi XML for " + addon_id)

    graph = {}
    for addon_id, item in nodes.items():
        version = item.get("version")
        if not version or "/" in version or "\\" in version:
            fail("Invalid version for " + addon_id)
        archive = PACKAGES / addon_id / (addon_id + "-" + version + ".zip")
        if not archive.is_file():
            fail("Missing install ZIP " + str(archive.relative_to(ROOT)))
        with ZipFile(archive) as z:
            files = z.namelist()
            if len(files) != len(set(files)):
                fail("Duplicate ZIP member in " + addon_id)
            if z.testzip() is not None:
                fail("Damaged ZIP " + addon_id)
            if any(f.startswith("/") or "\\" in f or ".." in PurePosixPath(f).parts
                   for f in files):
                fail("Unsafe ZIP member in " + addon_id)
            inner = ET.fromstring(z.read(addon_id + "/addon.xml"))
            if inner.get("id") != addon_id or inner.get("version") != version:
                fail("ZIP/addons.xml identity or version mismatch " + addon_id)
            if inner.get("name") != item.get("name"):
                fail("ZIP/addons.xml addon name mismatch " + addon_id)
            if imports(inner) != imports(item):
                fail("ZIP/addons.xml dependencies mismatch " + addon_id)
        metadata = item.find("./extension[@point='xbmc.addon.metadata']")
        if metadata is None:
            fail("Missing Kodi metadata extension for " + addon_id)
        assets = metadata.find("assets")
        if assets is not None:
            for asset in assets:
                path = (asset.text or "").strip()
                posix = PurePosixPath(path)
                if not path or path.startswith("/") or "\\" in path or "://" in path or ".." in posix.parts:
                    fail("Empty/unsafe metadata artwork path for " + addon_id)
                if not (PACKAGES / addon_id / path).is_file():
                    fail("Missing published artwork: packages/" + addon_id + "/" + path)

        graph[addon_id] = {d.get("addon") for d in item.findall("./requires/import")
                           if d.get("addon") in EXPECTED}
    # Even optional edges can confuse dependency traversal and should not cycle.
    seen, active = set(), set()
    def walk(node):
        if node in active:
            fail("Circular internal addon dependency involving " + node)
        if node in seen:
            return
        active.add(node)
        for dep in graph[node]:
            walk(dep)
        active.remove(node)
        seen.add(node)
    for node in graph:
        walk(node)
    print("PASS: 4 addon ZIPs, installer, repository index, hashes, metadata artwork, dependencies, and cycle checks")


if __name__ == "__main__":
    check()
