#!/usr/bin/env python3
"""Validate addon ZIPs and rebuild Kodi's repository manifest.

Only main is consumed by installed Kodi clients. Build on testing first.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
REPO_ID = "repository.nebelkind"
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]*$")


def make_repo_installer() -> None:
    addon_dir = ROOT / REPO_ID
    xml_file = addon_dir / "addon.xml"
    meta = ET.parse(xml_file).getroot()
    if meta.get("id") != REPO_ID:
        raise ValueError("The repository add-on ID changed unexpectedly")
    version = meta.attrib["version"]
    output_dir = PACKAGES / REPO_ID
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{REPO_ID}-{version}.zip"
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for file in sorted(addon_dir.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(ROOT).as_posix())
    print(f"Generated {output.relative_to(ROOT)}")


def describe_package(path: Path) -> tuple[str, str, ET.Element]:
    with ZipFile(path) as zf:
        entries = zf.namelist()
        if not entries or any(
            name.startswith("/") or ".." in Path(name).parts or "\\" in name
            for name in entries
        ):
            raise ValueError(f"Unsafe or empty ZIP: {path}")
        candidates = [name for name in entries if name.count("/") == 1 and name.endswith("/addon.xml")]
        if len(candidates) != 1:
            raise ValueError(f"Expected one top-level addon.xml: {path}")
        addon_dir = candidates[0].split("/")[0]
        addon_xml = ET.fromstring(zf.read(candidates[0]))
        addon_id, version = addon_xml.get("id"), addon_xml.get("version")
        if (not addon_id or not version or addon_id != addon_dir or
                not VERSION_PATTERN.fullmatch(version)):
            raise ValueError(f"Invalid id/version inside {path}")
        expected = PACKAGES / addon_id / f"{addon_id}-{version}.zip"
        if path != expected:
            raise ValueError(f"Wrong package path: {path}; expected {expected}")
        return addon_id, version, addon_xml


def version_key(version: str) -> tuple:
    parts = re.split(r"[._-]", version)
    return tuple((1, int(p)) if p.isdigit() else (0, p.casefold()) for p in parts)


def main() -> None:
    make_repo_installer()
    newest: dict[str, tuple[str, ET.Element]] = {}
    for path in sorted(PACKAGES.glob("*/*.zip")):
        addon_id, version, xml = describe_package(path)
        if addon_id not in newest or version_key(version) > version_key(newest[addon_id][0]):
            newest[addon_id] = (version, xml)
    root = ET.Element("addons")
    for addon_id, (_, xml) in sorted(newest.items()):
        root.append(xml)
    ET.indent(root, space="  ")
    document = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8") + b"\n"
    PACKAGES.mkdir(exist_ok=True)
    (PACKAGES / "addons.xml").write_bytes(document)
    (PACKAGES / "addons.xml.md5").write_text(hashlib.md5(document).hexdigest(), encoding="ascii")
    print(f"Indexed {len(newest)} addon(s). Publishing requires an explicit testing -> main merge.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, ET.ParseError, OSError) as exc:
        sys.exit(f"Repository build failed: {exc}")
