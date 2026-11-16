#!/usr/bin/env python3
"""Enforce NebelFlix Skin dependency only for Hybrid and TV addon ZIPs.

Keeps all published version numbers. Kodi auto-installs dependencies from
repositories where available, so this is not an access-control system.
"""
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
SKIN = "skin.nebelflix"
MINIMUM_SKIN_VERSION = "0.5.70"
PLUGINS = {
    "plugin.video.nebelflix.hybrid",
    "plugin.video.nebelflix.tv",
}
MUSIC = "plugin.video.nebelkindmusic"

def normalize(path):
    addon_id = path.parent.name
    if addon_id != SKIN and addon_id not in PLUGINS and addon_id != MUSIC:
        return False
    name = addon_id + "/addon.xml"
    with ZipFile(path) as source:
        metadata = ET.fromstring(source.read(name))
        if metadata.get("id") != addon_id:
            raise ValueError("Package addon ID mismatch: " + str(path))
        version = metadata.get("version")
        if path.name != addon_id + "-" + str(version) + ".zip":
            raise ValueError("Package addon version mismatch: " + str(path))
        requires = metadata.find("requires")
        if requires is None:
            requires = ET.Element("requires")
            metadata.insert(0, requires)
        changed = False
        if addon_id == SKIN:
            # Never reference Hybrid here, not even as an optional import:
            # Hybrid requires Skin and a reverse edge creates a cycle.
            for dependency in list(requires.findall("import")):
                if dependency.get("addon") == "plugin.video.nebelflix.hybrid":
                    requires.remove(dependency)
                    changed = True
        elif addon_id == MUSIC:
            # Music is a standalone addon. Remove any older Skin dependency.
            for dependency in list(requires.findall("import")):
                if dependency.get("addon") == SKIN:
                    requires.remove(dependency)
                    changed = True
        else:
            deps = [x for x in requires.findall("import") if x.get("addon") == SKIN]
            if len(deps) > 1:
                raise ValueError("Duplicate Skin dependency: " + str(path))
            if deps:
                dep = deps[0]
                if dep.get("version") != MINIMUM_SKIN_VERSION:
                    dep.set("version", MINIMUM_SKIN_VERSION)
                    changed = True
                if "optional" in dep.attrib:
                    del dep.attrib["optional"]
                    changed = True
            else:
                requires.append(ET.Element("import", {
                    "addon": SKIN, "version": MINIMUM_SKIN_VERSION
                }))
                changed = True
        if not changed:
            print("Dependency OK:", path)
            return False
        new_xml = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   + ET.tostring(metadata, encoding="utf-8"))
        descriptor, temp = tempfile.mkstemp(prefix="nebdep-", suffix=".zip", dir=str(path.parent))
        os.close(descriptor)
        try:
            with ZipFile(temp, "w") as target:
                for item in source.infolist():
                    payload = new_xml if item.filename == name else source.read(item.filename)
                    target.writestr(item, payload)
            os.replace(temp, path)
        finally:
            if os.path.isfile(temp):
                os.unlink(temp)
        print("Dependency applied:", path)
        return True

def main():
    for path in sorted(PACKAGES.glob("*/*.zip")):
        normalize(path)

if __name__ == "__main__":
    main()
