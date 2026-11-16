#!/usr/bin/env python3
"""Expand one owner-uploaded Nebelkind addon bundle on GitHub Actions.

Only the exact four known addon ZIPs are accepted. No arbitrary extraction.
Upload nebelkind-paket.zip to the repo root on main.
"""
from io import BytesIO
from pathlib import Path, PurePosixPath
import os
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile, BadZipFile

ROOT = Path(__file__).resolve().parents[1]
UPLOAD = ROOT / "nebelkind-paket.zip"
ALLOWED = {
    "skin.nebelflix",
    "plugin.video.nebelflix.hybrid",
    "plugin.video.nebelflix.tv",
    "plugin.video.nebelkindmusic",
}
MAX_PACKAGE = 25_000_000

def validate_inner(data, path):
    if len(data) > MAX_PACKAGE:
        raise ValueError("Addon ZIP too large: " + path)
    with ZipFile(BytesIO(data)) as zipfile:
        if zipfile.testzip() is not None:
            raise ValueError("Damaged addon ZIP: " + path)
        infos = zipfile.infolist()
        if any(i.filename.startswith("/") or ".." in PurePosixPath(i.filename).parts
                or "\\" in i.filename or i.file_size > 150_000_000 for i in infos):
            raise ValueError("Unsafe inner ZIP entry: " + path)
        xmls = [i.filename for i in infos if i.filename.count("/") == 1
                and i.filename.endswith("/addon.xml")]
        if len(xmls) != 1:
            raise ValueError("Expected one addon.xml at ZIP root: " + path)
        addon = ET.fromstring(zipfile.read(xmls[0]))
        addon_id, version = addon.get("id"), addon.get("version")
        if (addon_id not in ALLOWED or xmls[0] != addon_id + "/addon.xml"
                or path != "packages/" + addon_id + "/" + addon_id + "-" + str(version) + ".zip"):
            raise ValueError("Unexpected package ID or version: " + path)

def main():
    if not UPLOAD.is_file():
        print("No incoming Nebelkind bundle")
        return
    if UPLOAD.stat().st_size > 25_000_000:
        raise ValueError("Upload bundle exceeds 25 MB")
    pending = []
    with ZipFile(UPLOAD) as bundle:
        files = [x for x in bundle.infolist() if not x.is_dir()]
        if len(files) != 4:
            raise ValueError("Bundle must contain exactly four addon ZIPs")
        seen = set()
        for item in files:
            path = item.filename
            parts = PurePosixPath(path).parts
            if len(parts) != 3 or parts[0] != "packages" or parts[1] not in ALLOWED:
                raise ValueError("Unexpected bundle path: " + path)
            if parts[1] in seen:
                raise ValueError("Duplicate addon in bundle: " + parts[1])
            seen.add(parts[1])
            if item.file_size > MAX_PACKAGE:
                raise ValueError("Bundle member too large: " + path)
            payload = bundle.read(item)
            validate_inner(payload, path)
            pending.append((ROOT.joinpath(*parts), payload))
    # Only write any package after the entire bundle has passed validation.
    for target, payload in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".zip.tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, target)
        print("Imported", target.relative_to(ROOT))
    UPLOAD.unlink()
    print("Bundle consumed; Kodi index is prepared by the workflow")

if __name__ == "__main__":
    try:
        main()
    except (BadZipFile, KeyError, ValueError, OSError, ET.ParseError) as exc:
        sys.exit("Invalid Nebelkind upload: " + str(exc))
