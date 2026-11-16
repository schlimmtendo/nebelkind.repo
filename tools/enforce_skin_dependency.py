#!/usr/bin/env python3
"""Keep Nebelkind Video addons independent of the NebelFlix skin.\nPreserve required functional dependencies such as Vavoo TV Plus.\n"""
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
SKIN = "skin.nebelflix"
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
        # All our addons must be browsable/installable independently of the
        # cosmetic skin. Removing this import also prevents repository
        # dependency resolution involving the skin from blocking the dialog.
        for dependency in list(requires.findall("import")):
            if dependency.get("addon") == SKIN and addon_id != SKIN:
                requires.remove(dependency)
                changed = True
            elif addon_id == SKIN and dependency.get("addon") == "plugin.video.nebelflix.hybrid":
                requires.remove(dependency)
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
