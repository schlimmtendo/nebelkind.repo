#!/usr/bin/env python3
"""Build only the changed Kodi addon ZIP from GitHub-tracked override files.

The first complete ZIP must already exist in packages/. Afterwards, GitHub
text file edits under overrides/<addon-id>/ can update only that addon.
release_versions.json controls when Kodi sees a strictly newer version.
All work stays in the Actions worktree until the workflow's final commit.
"""
from __future__ import annotations
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
OVERRIDES = ROOT / "overrides"
VERSION_FILE = ROOT / "release_versions.json"
ALLOWED = {
    "skin.nebelflix",
    "plugin.video.nebelflix.hybrid",
    "plugin.video.nebelflix.tv",
    "plugin.video.nebelkindmusic",
}
VERSION = re.compile(r"^[0-9][0-9A-Za-z.+_-]*$")


def version_key(s):
    return tuple((1, int(p)) if p.isdigit() else (0, p.lower())
                 for p in re.split(r"[._-]", s))


def release_plan():
    if not VERSION_FILE.exists():
        return {}
    plan = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or any(
        name not in ALLOWED or not isinstance(v, str) or not VERSION.fullmatch(v)
        for name, v in plan.items()
    ):
        raise ValueError("Invalid release_versions.json; keys must be known addon IDs")
    return plan


def newest_zip(addon_id):
    candidates = sorted((PACKAGES / addon_id).glob(addon_id + "-*.zip"))
    checked = []
    for path in candidates:
        with ZipFile(path) as z:
            if z.testzip() is not None:
                raise ValueError("Invalid ZIP: " + str(path))
            xml = ET.fromstring(z.read(addon_id + "/addon.xml"))
            version = xml.get("version")
            if xml.get("id") != addon_id or path.name != addon_id + "-" + str(version) + ".zip":
                raise ValueError("Invalid addon identity: " + str(path))
            checked.append((version_key(version), version, path))
    if not checked:
        raise ValueError("No base ZIP for " + addon_id + "; import the initial addon first")
    return max(checked)


def files_for(addon_id):
    directory = OVERRIDES / addon_id
    if not directory.exists():
        return {}
    result = {}
    for file in sorted(directory.rglob("*")):
        if not file.is_file() or file.name == ".gitkeep":
            continue
        rel = file.relative_to(directory).as_posix()
        if any(p in ("..", ".") for p in PurePosixPath(rel).parts) or rel.startswith("/"):
            raise ValueError("Unsafe override file: " + rel)
        result[addon_id + "/" + rel] = file.read_bytes()
    return result


def build(addon_id, intended_version):
    _, old_version, source = newest_zip(addon_id)
    version = intended_version or old_version
    if version_key(version) < version_key(old_version):
        raise ValueError("Refusing version downgrade for " + addon_id)
    patches = files_for(addon_id)
    with ZipFile(source) as z:
        old_xml = ET.fromstring(z.read(addon_id + "/addon.xml"))
        old_xml_bytes = z.read(addon_id + "/addon.xml")
        new_xml_bytes = patches.pop(addon_id + "/addon.xml", old_xml_bytes)
        root = ET.fromstring(new_xml_bytes)
        if root.get("id") != addon_id:
            raise ValueError("Addon ID cannot change: " + addon_id)
        root.set("version", version)
        if version != old_version or new_xml_bytes != old_xml_bytes:
            new_xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")
        patches[addon_id + "/addon.xml"] = new_xml_bytes

        # Do not rewrite ZIPs if nothing has changed.
        old_names = set(z.namelist())
        modified = (version != old_version or any(
            key not in old_names or z.read(key) != value
            for key, value in patches.items()
        ))
        if not modified:
            print("Unchanged:", addon_id, version)
            return

        output_dir = PACKAGES / addon_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / (addon_id + "-" + version + ".zip")
        handle, temp = tempfile.mkstemp(prefix="neb-release-", suffix=".zip", dir=output_dir)
        os.close(handle)
        try:
            with ZipFile(temp, "w") as target:
                for entry in z.infolist():
                    if entry.is_dir():
                        target.writestr(entry, b"")
                    else:
                        target.writestr(entry, patches.pop(entry.filename, z.read(entry.filename)))
                for name, content in sorted(patches.items()):
                    target.writestr(name, content)
            with ZipFile(temp) as checked:
                if checked.testzip() is not None:
                    raise ValueError("Output ZIP failed validation: " + str(output))
                xml = ET.fromstring(checked.read(addon_id + "/addon.xml"))
                if xml.get("id") != addon_id or xml.get("version") != version:
                    raise ValueError("Output addon version mismatch")
            os.replace(temp, output)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        print("Prepared:", output.relative_to(ROOT))


def main():
    plan = release_plan()
    ids = set(plan)
    if OVERRIDES.is_dir():
        ids |= {p.name for p in OVERRIDES.iterdir() if p.is_dir() and p.name in ALLOWED}
        unexpected = {p.name for p in OVERRIDES.iterdir() if p.is_dir() and p.name not in ALLOWED}
        if unexpected:
            raise ValueError("Unknown override directories: " + ", ".join(sorted(unexpected)))
    for addon_id in sorted(ids):
        build(addon_id, plan.get(addon_id))


if __name__ == "__main__":
    main()
