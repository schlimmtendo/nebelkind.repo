#!/usr/bin/env python3
"""The previous Hybrid auto-update loop must stay disabled for preload safety."""
from pathlib import Path
import importlib.util
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "overrides/plugin.video.nebelflix.hybrid/resources/lib/nebelkind_update_status.py"
source = FILE.read_text(encoding="utf-8")
compile(source, str(FILE), "exec")
assert "def run_startup_check():" in source
assert "urlopen" not in source
assert "UpdateAddonRepos" not in source
assert "InstallAddon" not in source
spec = importlib.util.spec_from_file_location("legacy_hybrid_update", FILE)
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
assert legacy.run_startup_check() is None
print("PASS: Hybrid legacy update loop disabled; Kodi handles repository updates")
