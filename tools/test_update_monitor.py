#!/usr/bin/env python3
"""Offline regression tests for Nebelkind's future Kodi auto-update watcher."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "overrides/plugin.video.nebelflix.hybrid/resources/lib/nebelkind_update_status.py"
if not FILE.is_file():
    raise AssertionError("New updater override is missing")
compile(FILE.read_bytes(), str(FILE), "exec")
spec = importlib.util.spec_from_file_location("nebelkind_update_status_for_test", FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

assert module._pending(
    {"skin.nebelflix": "0.5.72", "plugin.video.nebelflix.hybrid": "2026.11.15.3"},
    {"skin.nebelflix": "0.5.71", "plugin.video.nebelflix.hybrid": "2026.11.15.3"}
) == {"skin.nebelflix": "0.5.72"}
assert module._pending({"skin.nebelflix": "0.5.71"}, {"skin.nebelflix": "0.5.71"}) == {}
assert module._vkey("2026.11.15.10") > module._vkey("2026.11.15.9")
assert module._vkey("0.5.71") > module._vkey("0.5.70")

with TemporaryDirectory(prefix="nebelkind-state-test-") as directory:
    file = str(Path(directory) / "state.json")
    assert module._read_state(file) == {}
    module._save_state(file, {"last_check": 123, "installed": {"skin.nebelflix": "0.5.71"}})
    assert module._read_state(file)["installed"]["skin.nebelflix"] == "0.5.71"

class FakeKodi:
    LOGINFO = 1
    LOGWARNING = 2
    def __init__(self, value):
        self.value = value
    def executeJSONRPC(self, request):
        o = json.loads(request)
        assert o["method"] == "Settings.GetSettingValue"
        assert o["params"]["setting"] == "addons.autoUpdates"
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"value": self.value}})

assert module._update_mode(FakeKodi(0)) == 0
assert module._update_mode(FakeKodi(1)) == 1
assert module._update_mode(FakeKodi(2)) == 2
assert module._update_mode(FakeKodi("n/a")) is None

class Response:
    def __init__(self, contents):
        self.contents = contents
    def read(self, limit):
        return self.contents[:limit]
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False

def fake_response(document):
    return Response(json.dumps(document).encode("utf-8"))

manifest = {"schema": 1, "addons": [
    {"id": "skin.nebelflix", "version": "0.5.72"},
    {"id": "plugin.video.nebelflix.hybrid", "version": "2026.11.15.3"},
]}
with patch.object(module, "urlopen", return_value=fake_response(manifest)):
    assert module._published_versions()["skin.nebelflix"] == "0.5.72"
with patch.object(module, "urlopen", return_value=fake_response({"schema": 2, "addons": []})):
    try:
        module._published_versions()
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid release schema accepted")
with patch.object(module, "urlopen", return_value=fake_response(
    {"schema": 1, "addons": [manifest["addons"][0], manifest["addons"][0]]})):
    try:
        module._published_versions()
    except ValueError:
        pass
    else:
        raise AssertionError("Duplicate update manifest IDs accepted")

print("PASS: watcher syntax, version compare, state, update policy, manifest validation")
