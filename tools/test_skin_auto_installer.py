#!/usr/bin/env python3
"""Offline safety tests for NebelFlix first-run/second-run auto-installer."""
import importlib.util
import json
from pathlib import Path
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "overrides/skin.nebelflix/resources/auto_installer.py"
spec = importlib.util.spec_from_file_location("skin_auto_installer_for_test", MODULE)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

assert installer._key("0.5.72") > installer._key("0.5.71")
assert installer._key("2026.11.15.11") > installer._key("2026.11.15.9")
installed = {
    "skin.nebelflix": "0.5.71",
    "plugin.video.nebelflix.hybrid": "2026.11.15.3",
    "plugin.video.nebelkindmusic": "5.0.0",
}
online = {
    "skin.nebelflix": "0.5.72",
    "plugin.video.nebelflix.hybrid": "2026.11.15.4",
    "plugin.video.nebelkindmusic": "5.0.0",
    "plugin.video.nebelflix.tv": "0.2.0",
}
assert installer._needed(online, installed) == {
    "plugin.video.nebelflix.hybrid": "2026.11.15.4",
    "plugin.video.nebelflix.tv": "0.2.0",
    "skin.nebelflix": "0.5.72",
}

with tempfile.TemporaryDirectory() as folder:
    p = Path(folder)
    def ready(pid=400, flag="false"):
        return installer._cache_ready(p, pid=pid, prepare_flag=flag)
    assert not ready(), "No widget cache must BLOCK updates"
    installer._write(p / "widget-preload-state.json", {
        "completed_at": 9999, "failed": 0, "planned": 100})
    assert not ready(), "Absent artwork state must BLOCK"
    installer._write(p / "artwork-preload-v1.json", {"completed": True})
    assert not ready(), "Absent widget cache must BLOCK"
    installer._write(p / "widget-cache.json", {"nf://genre/x": {"items": []}})
    assert not ready(), "One cached row cannot satisfy a 100-row preload"
    installer._write(p / "widget-preload-state.json", {
        "completed_at": 9999, "failed": 0, "planned": 1})
    assert ready(), "Prior complete boot with valid cache should permit updates"
    assert not ready(flag="true"), "Active preload must BLOCK"
    (p / "widget-cache-reset-request.json").write_text("{}")
    assert not ready(), "Pending reset must BLOCK"
    (p / "widget-cache-reset-request.json").unlink()
    installer._write(p / "widget-preload-state.json", {
        "completed_at": 9999, "failed": 1, "planned": 1})
    assert not ready(), "Failed row preload must BLOCK"
    installer._write(p / "widget-preload-state.json", {
        "completed_at": 9999, "failed": 0, "planned": 1})
    installer._write(p / "auto-installer-boot-gate.json", {
        "awaiting_restart": True, "prepared_pid": 400})
    assert not ready(pid=400), "Same first-run Kodi process must BLOCK"
    assert ready(pid=401), "Later Kodi process must permit updates"
    (p / "widget-preload-state.json").unlink()
    assert not ready(pid=401), "Deleted preload state must BLOCK"

version_plan = json.loads((ROOT / "release_versions.json").read_text(encoding="utf-8"))
assert version_plan["skin.nebelflix"] == "0.5.72"
skin = ROOT / "packages/skin.nebelflix/skin.nebelflix-0.5.72.zip"
with ZipFile(skin) as z:
    assert z.testzip() is None
    source = z.read("skin.nebelflix/resources/controller.py").decode("utf-8")
    assert "# NEBELFLIX_SKIN_AUTOINSTALLER_V1" in source
    assert "mark_preload_complete()" in source
    assert "first_full_setup and boot_gate_saved" in source
    assert "and not artwork_upgrade" not in source
    assert "skin.nebelflix/resources/auto_installer.py" in z.namelist()
    compile(source, "skin.nebelflix/resources/controller.py", "exec")

hybrid = ROOT / "packages/plugin.video.nebelflix.hybrid/plugin.video.nebelflix.hybrid-2026.11.15.4.zip"
with ZipFile(hybrid) as z:
    old_service = z.read(
        "plugin.video.nebelflix.hybrid/resources/lib/nebelkind_update_status.py").decode("utf-8")
    assert "def run_startup_check():" in old_service
    assert "return None" in old_service
    assert "urlopen(" not in old_service, "Old Hybrid must not check for updates during preload"
print("PASS: fresh cache blocked, reset/error blocked, new process permitted; live ZIP packaging verified")
