#!/usr/bin/env python3
"""Regression checks for Kodi TV guide Back button / Backspace navigation."""
import ast
import sys
import json
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
AID = "plugin.video.nebelflix.tv"
VERSION=json.loads((ROOT / "release_versions.json").read_text(encoding="utf-8"))[AID]
ARCHIVE = ROOT / "packages" / AID / (AID + "-" + VERSION + ".zip")
SOURCE = AID + "/resources/lib/guide.py"

with ZipFile(ARCHIVE) as z:
    assert z.testzip() is None
    if SOURCE not in z.namelist():
        bridge = z.read(AID + "/resources/lib/vavoo_bridge.py").decode("utf-8")
        assert "favorites_epg" in bridge and "plugin.video.vavoo.tv.plus" in bridge
        assert AID + "/resources/skins/Default/1080i/Guide.xml" not in z.namelist()
        compile(bridge, "vavoo_bridge.py", "exec")
        print("PASS: native Vavoo guide delegation; obsolete local guide and XML removed")
        sys.exit(0)
    contents = z.read(SOURCE).decode("utf-8")
    compile(contents, SOURCE, "exec")
    tree = ast.parse(contents)
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.startswith("ACTION_"):
                constants[target.id] = ast.literal_eval(node.value)
    assert constants["ACTION_PARENT_DIR"] == 9
    assert constants["ACTION_PREVIOUS_MENU"] == 10
    assert constants["ACTION_NAV_BACK"] == 92
    assert constants["ACTION_CONTEXT_MENU"] == 117
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Guide")
    handler = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "onAction")
    handlers = ast.unparse(handler)
    assert "(ACTION_PARENT_DIR, ACTION_PREVIOUS_MENU, ACTION_NAV_BACK)" in handlers
    assert "self.setFocusId(16)" in handlers
    assert "self.context_menu()" in handlers, "Guide context menu must offer explicit favorite actions"
    assert "elif controlId == 16" in ast.unparse(next(
        n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "onClick"))
print("PASS: TV guide handles Backspace/ParentDir, Back, remote navigation, context menu and Zurueck")


import runpy
runpy.run_path(str(ROOT / "tools/test_revision_20261119.py"), run_name="__main__")
