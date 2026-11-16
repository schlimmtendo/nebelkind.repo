#!/usr/bin/env python3
"""Prune superseded Kodi ZIPs only after validating the COMPLETE new index.

Run after creating the packages/addons.xml and release.json in the Actions
worktree. Deletions and new files are published together in one Git commit:
a failed validation or failed push leaves all old packages on GitHub.
"""
from pathlib import Path
import json
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
ALLOWED = {
    "repository.nebelkind",
    "skin.nebelflix",
    "plugin.video.nebelflix.hybrid",
    "plugin.video.nebelflix.tv",
    "plugin.video.nebelkindmusic",
}


def version_key(value):
    return tuple((1, int(p)) if p.isdigit() else (0, p.lower())
                 for p in re.split(r"[._-]", value))


def main():
    manifest = ET.parse(PACKAGES / "addons.xml").getroot()
    listed = {x.get("id"): x.get("version") for x in manifest.findall("addon")}
    if len(listed) != len(manifest.findall("addon")):
        raise ValueError("Duplicate IDs in addons.xml")
    if not set(listed).issubset(ALLOWED):
        raise ValueError("Unexpected addon IDs in addons.xml")
    releases = json.loads((PACKAGES / "release.json").read_text(encoding="utf-8"))
    claimed = {x["id"]: x["version"] for x in releases["addons"]}
    if claimed != {k: v for k, v in listed.items() if k != "repository.nebelkind"}:
        raise ValueError("release.json disagrees with addons.xml")

    outdated = []
    groups = sorted(p for p in PACKAGES.iterdir() if p.is_dir())
    for directory in groups:
        addon_id = directory.name
        if addon_id not in ALLOWED:
            raise ValueError("Unexpected package directory " + addon_id)
        versions = []
        for file in sorted(directory.glob("*.zip")):
            with ZipFile(file) as z:
                if z.testzip() is not None:
                    raise ValueError("Corrupt ZIP: " + str(file))
                addon = ET.fromstring(z.read(addon_id + "/addon.xml"))
                version = addon.get("version")
                if (addon.get("id") != addon_id or
                        file.name != addon_id + "-" + str(version) + ".zip"):
                    raise ValueError("ZIP filename/metadata mismatch: " + str(file))
                versions.append((version_key(version), version, file))
        if not versions:
            continue
        newest = max(versions)
        if listed.get(addon_id) != newest[1]:
            raise ValueError("Index is not pointing to newest " + addon_id)
        outdated.extend(item[2] for item in versions if item[2] != newest[2])

    # Nothing has been deleted before every package and the index passed checks.
    for file in outdated:
        file.unlink()
        print("Retired older package:", file.relative_to(ROOT))
    print("Current package index verified; outdated ZIPs:", len(outdated))


if __name__ == "__main__":
    main()
