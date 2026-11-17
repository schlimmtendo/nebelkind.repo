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

assert installer._key("0.5.77") > installer._key("0.5.76")
assert installer._check_due(10000, 10010, first_check_in_session=True), (
    "A Kodi restart MUST cause an immediate release check")
assert not installer._check_due(10000, 10010, first_check_in_session=False), (
    "A second check in the same session must respect the interval")
assert installer._check_due(10000, 10000 + installer.INTERVAL, False)
assert installer._check_due("corrupt state", 10010, False)

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
assert version_plan["skin.nebelflix"] == "0.5.80"
skin = ROOT / "packages/skin.nebelflix/skin.nebelflix-0.5.80.zip"
with ZipFile(skin) as z:
    assert z.testzip() is None
    source = z.read("skin.nebelflix/resources/controller.py").decode("utf-8")
    assert "# NEBELFLIX_SKIN_AUTOINSTALLER_V1" in source
    assert "# NEBELFLIX_INSTALL_PAUSE_V2" in source
    assert "if prop('UpdatePause') == 'true':" in source
    assert "len(widgets.inflight)" in source
    assert "if prop('StopController') == 'true':" in source
    assert "mark_preload_complete()" in source
    assert "first_full_setup and boot_gate_saved" in source
    assert "and not artwork_upgrade" not in source
    assert "skin.nebelflix/resources/auto_installer.py" in z.namelist()

    dialog_files = [n for n in z.namelist() if n.endswith("/DialogProgress.xml")]
    assert dialog_files, "A modal Kodi progress dialog must have a Skin XML layout"
    progress_xml = z.read(dialog_files[0]).decode("utf-8")
    import xml.etree.ElementTree as ET
    parsed = ET.fromstring(progress_xml)
    assert parsed.tag == "window", "Invalid modal progress window XML"
    print("Modal Kodi progress layout:", dialog_files[0])

    compile(source, "skin.nebelflix/resources/controller.py", "exec")

hybrid = ROOT / "packages/plugin.video.nebelflix.hybrid/plugin.video.nebelflix.hybrid-2026.11.15.5.zip"
with ZipFile(hybrid) as z:
    old_service = z.read(
        "plugin.video.nebelflix.hybrid/resources/lib/nebelkind_update_status.py").decode("utf-8")
    assert "def run_startup_check():" in old_service
    assert "return None" in old_service
    assert "urlopen(" not in old_service, "Old Hybrid must not check for updates during preload"

# The repository refresh is the ONLY supported Kodi update trigger.
# Exercise real control flow with Kodi API doubles, without a running GUI.
from types import SimpleNamespace
from unittest.mock import patch
import sys

calls = []
install_succeeds = [True]
installed_versions = {"skin.nebelflix": "0.5.76"}
clock = [0]

def fake_clock():
    clock[0] += 4
    return clock[0]

class Dialog:
    def create(self, *args, **kwargs): calls.append(("modal_create", args))
    def update(self, percent, *args, **kwargs): calls.append(("modal_progress", percent))
    def close(self): calls.append(("modal_close", None))

class Monitor:
    def abortRequested(self): return False
    def waitForAbort(self, duration):
        # Kodi confirms Quit (but only if the update is confirmed installed).
        return duration == 8 and ("builtin", "Quit") in calls

def builtin(command):
    calls.append(("builtin", command))
    if command == "UpdateAddonRepos" and install_succeeds[0]:
        installed_versions["skin.nebelflix"] = "0.5.77"

mode = [0]
settings_write_succeeds = [True]
def jsonrpc(payload):
    req = json.loads(payload)
    assert req["params"]["setting"] == "addons.updatemode"
    if req["method"] == "Settings.GetSettingValue":
        return json.dumps({"jsonrpc": "2.0", "id": req["id"],
                           "result": {"value": mode[0]}})
    if req["method"] == "Settings.SetSettingValue":
        calls.append(("setting", req["params"]["value"]))
        if settings_write_succeeds[0]:
            mode[0] = req["params"]["value"]
            return json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": True})
        return json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": False})
    raise AssertionError("Unexpected Kodi JSON-RPC method")

fake_xbmc = SimpleNamespace(executebuiltin=builtin, executeJSONRPC=jsonrpc)
fake_gui = SimpleNamespace(DialogProgress=lambda: Dialog())

with tempfile.TemporaryDirectory() as folder:
    state_file = Path(folder) / "auto-installer-state.json"
    patched = [patch.dict(sys.modules, {"xbmc": fake_xbmc, "xbmcgui": fake_gui}),
               patch.object(installer, "_safe_to_install", return_value=True),
               patch.object(installer, "_installed", side_effect=lambda: dict(installed_versions)),
               patch.object(installer, "_pause_widget_work", return_value=True),
               patch.object(installer, "_verify_idle_before_exit", return_value=True),
               patch.object(installer, "_resume_widget_work", return_value=None),
               patch.object(installer, "_log", return_value=None),
               patch.object(installer, "_toast", return_value=None),
               patch.object(installer.time, "monotonic", side_effect=fake_clock)]
    with patched[0], patched[1], patched[2], patched[3], patched[4], \
         patched[5], patched[6], patched[7], patched[8]:
        # Success: only Kodi's native repository refresh may change a version.
        success_state = {}
        assert installer._run_install({"skin.nebelflix": "0.5.77"},
                                      success_state, state_file, Monitor())
        assert calls[0][0] == "modal_create"
        assert ("builtin", "UpdateAddonRepos") in calls
        assert not any("InstallAddon" in str(x) for x in calls)
        assert ("modal_progress", 100) in calls
        assert ("builtin", "Quit") in calls

        # Timeout: native Kodi updater has not installed anything. UI unlocks,
        # state clears, no 100% and no forced quit.
        calls.clear()
        installed_versions["skin.nebelflix"] = "0.5.76"
        install_succeeds[0] = False
        failure_state = {}
        assert not installer._run_install({"skin.nebelflix": "0.5.77"},
                                          failure_state, state_file, Monitor())
        assert ("builtin", "UpdateAddonRepos") in calls
        assert ("modal_close", None) in calls
        assert ("builtin", "Quit") not in calls
        assert ("modal_progress", 100) not in calls
        assert "active" not in failure_state

        # Unfinished widget jobs: the native updater must NEVER run.
        calls.clear()
        with patch.object(installer, "_pause_widget_work", return_value=False):
            assert not installer._run_install(
                {"skin.nebelflix": "0.5.77"}, {}, state_file, Monitor())
        assert ("builtin", "UpdateAddonRepos") not in calls
        assert ("modal_close", None) in calls

        # User requested automatic updating, but Kodi is still on mode 1
        # (notify only). Correct Kodi's global preference once, verify it,
        # and then use the native repository updater.
        calls.clear()
        mode[0] = 1
        install_succeeds[0] = True
        installed_versions["skin.nebelflix"] = "0.5.76"
        assert installer._run_install(
            {"skin.nebelflix": "0.5.77"}, {}, state_file, Monitor())
        assert ("setting", 0) in calls
        assert mode[0] == 0
        assert ("builtin", "UpdateAddonRepos") in calls
        assert ("builtin", "Quit") in calls

        # Mode 2 is an explicit opt-out: do not touch it.
        calls.clear()
        mode[0] = 2
        assert not installer._run_install(
            {"skin.nebelflix": "0.5.77"}, {}, state_file, Monitor())
        assert not calls

        # If Kodi refuses the settings change, no fake installation is shown.
        calls.clear()
        mode[0] = 1
        settings_write_succeeds[0] = False
        assert not installer._run_install(
            {"skin.nebelflix": "0.5.77"}, {}, state_file, Monitor())
        assert ("setting", 0) in calls
        assert not any(x[0] == "builtin" for x in calls)
        assert not any(x[0] == "modal_create" for x in calls)

    source = MODULE.read_text(encoding="utf-8")
    assert 'InstallAddon(' not in source, "A broken built-in would cause hangs again"
    assert 'WAIT_PER_ADDON' not in source
    assert 'state.pop("active", None)' in source
    assert 'RETRY = 60 * 60' in source
