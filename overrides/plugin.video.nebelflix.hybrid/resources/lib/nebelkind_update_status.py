# -*- coding: utf-8 -*-
"""Nebelkind update watcher. Installs are ALWAYS performed by Kodi itself.

The background indicator counts *confirmed installed addons*, not downloaded
bytes. Kodi's own installer owns the actual ZIP download/progress and
dependencies. No filesystem replacement, forced exit or made-up percentage.
"""
import json
import os
import re
import time
from urllib.request import Request, urlopen

MANIFEST_URL = (
    "https://raw.githubusercontent.com/schlimmtendo/nebelkind.repo/"
    "main/packages/release.json"
)
ADDON_IDS = (
    "skin.nebelflix",
    "plugin.video.nebelflix.hybrid",
    "plugin.video.nebelflix.tv",
    "plugin.video.nebelkindmusic",
)
NAMES = {
    "skin.nebelflix": "NebelFlix Skin",
    "plugin.video.nebelflix.hybrid": "Nebelkind Hybrid",
    "plugin.video.nebelflix.tv": "NebelFlix TV",
    "plugin.video.nebelkindmusic": "Nebelkind Music",
}
CHECK_INTERVAL = 30 * 60
HTTP_TIMEOUT = 2.5
INSTALL_WAIT = 150
POLL_SECONDS = 5
VERSION = re.compile(r"^[0-9][0-9A-Za-z.+_-]{0,47}$")


def _vkey(value):
    parts = []
    for piece in re.split(r"[._-]", str(value)):
        if piece:
            parts.append((1, int(piece)) if piece.isdigit() else (0, piece.lower()))
    return tuple(parts)


def _pending(published, installed):
    return {addon_id: version for addon_id, version in published.items()
            if addon_id in installed and _vkey(version) > _vkey(installed[addon_id])}


def _read_state(filename):
    try:
        with open(filename, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (IOError, OSError, ValueError, TypeError):
        return {}


def _save_state(filename, state):
    folder = os.path.dirname(filename)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    temp = filename + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, sort_keys=True)
    os.replace(temp, filename)


def _published_versions():
    request = Request(MANIFEST_URL, headers={
        "User-Agent": "NebelkindKodiUpdater/2.0",
        "Cache-Control": "no-cache",
    })
    with urlopen(request, timeout=HTTP_TIMEOUT) as response:
        raw = response.read(131073)
    if len(raw) > 131072:
        raise ValueError("Manifest too large")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("Invalid Nebelkind release manifest")
    addons = data.get("addons")
    if not isinstance(addons, list):
        raise ValueError("Missing addon list in manifest")
    found = {}
    for item in addons:
        if not isinstance(item, dict):
            continue
        addon_id = item.get("id")
        version = item.get("version")
        if addon_id in ADDON_IDS and isinstance(version, str) and VERSION.fullmatch(version):
            if addon_id in found:
                raise ValueError("Duplicate addon in manifest")
            found[addon_id] = version
    return found


def _installed_versions(xbmcaddon):
    result = {}
    for addon_id in ADDON_IDS:
        try:
            result[addon_id] = xbmcaddon.Addon(id=addon_id).getAddonInfo("version")
        except Exception:
            pass
    return result


def _update_mode(xbmc):
    """Kodi Settings: 0=install automatically, 1=notify, 2=never."""
    request = {"jsonrpc": "2.0", "id": 1, "method": "Settings.GetSettingValue",
               "params": {"setting": "addons.autoUpdates"}}
    try:
        data = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
        value = data.get("result", {}).get("value")
        return value if type(value) is int and value in (0, 1, 2) else None
    except Exception:
        return None


def _log(xbmc, message, warning=False):
    xbmc.log("[Nebelkind Updater] " + message,
             xbmc.LOGWARNING if warning else xbmc.LOGINFO)


def _toast(xbmcgui, heading, message, duration=7000):
    xbmcgui.Dialog().notification(heading, message,
                                  xbmcgui.NOTIFICATION_INFO, duration, False)


def _completion_notice(xbmc, xbmcgui, state, installed):
    previous = state.get("installed")
    if not isinstance(previous, dict):
        previous = {}
    completed = [addon_id for addon_id, now in installed.items()
                 if addon_id in previous and _vkey(now) > _vkey(previous[addon_id])]
    if completed:
        labels = ", ".join(NAMES[addon_id] for addon_id in completed)
        _toast(xbmcgui, "Nebelkind · Update abgeschlossen", labels, 8000)
        _log(xbmc, "Installed new versions: " + ", ".join(
            addon_id + "=" + installed[addon_id] for addon_id in completed))
    # Persist even when nothing changed. Never invent a first-start completion.
    state["installed"] = installed
    return completed


def _wait_for_native_install(xbmc, xbmcaddon, xbmcgui, monitor, pending, state, state_path):
    """Observe versions changed by Kodi's own installer. No own ZIP writes."""
    dialog = None
    installed_initial = set()
    completed = set()
    try:
        dialog = xbmcgui.DialogProgressBG()
        dialog.create("Nebelkind · Update", "Kodi installiert Updates. Bitte warten ...")
        dialog.update(0, message="Installiert: 0 von %d Addons" % len(pending))
        _log(xbmc, "Native auto-update requested for " + ", ".join(sorted(pending)))
        started = time.monotonic()
        while time.monotonic() - started < INSTALL_WAIT:
            if monitor.waitForAbort(POLL_SECONDS):
                return
            current = _installed_versions(xbmcaddon)
            completed = {addon_id for addon_id, version in pending.items()
                         if addon_id in current and _vkey(current[addon_id]) >= _vkey(version)}
            if completed != installed_initial:
                installed_initial = completed
                percent = len(completed) * 100 // len(pending)
                dialog.update(percent, message="Installiert: %d von %d Addons" %
                              (len(completed), len(pending)))
            if len(completed) == len(pending):
                # Only the installed Kodi version is proof of completion.
                state["installed"] = current
                state["last_announced"] = ""
                state["last_incomplete"] = ""
                _save_state(state_path, state)
                _toast(xbmcgui, "Nebelkind · Updates installiert",
                       "%d von %d Addons erfolgreich aktualisiert" %
                       (len(completed), len(pending)), 8000)
                _log(xbmc, "All pending releases confirmed installed")
                return
        if state.get("last_incomplete") != state.get("last_announced"):
            state["last_incomplete"] = state.get("last_announced")
            _toast(xbmcgui, "Nebelkind · Update ausstehend",
                   "Kodi hat noch nicht alle Updates installiert.", 8000)
            _log(xbmc, "Native install was not confirmed within timeout", True)
        state["installed"] = _installed_versions(xbmcaddon)
        _save_state(state_path, state)
    finally:
        if dialog is not None:
            try:
                dialog.close()
            except Exception:
                pass


def run_startup_check():
    """Monitor updates after normal Kodi startup; never force app termination.

    Requires the Hybrid service to remain alive. Uses Kodi's update setting:
    auto = native install; notify/never = announcement only.
    """
    import xbmc
    import xbmcaddon
    import xbmcgui
    import xbmcvfs

    monitor = xbmc.Monitor()
    try:
        addon = xbmcaddon.Addon(id="plugin.video.nebelflix.hybrid")
        state_path = os.path.join(
            xbmcvfs.translatePath(addon.getAddonInfo("profile")),
            "nebelkind_update_status.json")
    except Exception as exc:
        _log(xbmc, "Could not open updater profile: " + type(exc).__name__, True)
        return
    state = _read_state(state_path)
    # Let the startup widgets render and Kodi finish its own startup checks.
    if monitor.waitForAbort(12):
        return
    first = True
    while not monitor.abortRequested():
        try:
            installed = _installed_versions(xbmcaddon)
            _completion_notice(xbmc, xbmcgui, state, installed)
            _save_state(state_path, state)
            now = int(time.time())
            if not first and now - int(state.get("last_check", 0) or 0) < CHECK_INTERVAL:
                if monitor.waitForAbort(20):
                    return
                continue
            first = False
            state["last_check"] = now
            if "repository.nebelkind" not in _check_repository_installed(xbmcaddon):
                _log(xbmc, "Nebelkind repo missing, no background install", True)
                _save_state(state_path, state)
                if monitor.waitForAbort(30):
                    return
                continue
            versions = _published_versions()
            pending = _pending(versions, installed)
            signature = ";".join("%s:%s" % item for item in sorted(pending.items()))
            mode = _update_mode(xbmc)
            if not pending:
                state["last_announced"] = ""
                state["last_incomplete"] = ""
                _save_state(state_path, state)
                if monitor.waitForAbort(20):
                    return
                continue
            if signature != state.get("last_announced"):
                message = (NAMES[next(iter(pending))] if len(pending) == 1
                           else str(len(pending)) + " Nebelkind-Addons")
                _toast(xbmcgui, "Nebelkind · Update verfügbar", message, 8500)
                _log(xbmc, "New versions available: " + signature)
            state["last_announced"] = signature
            _save_state(state_path, state)
            if mode == 2 or mode == 1:
                if state.get("last_mode_warning") != signature:
                    _toast(xbmcgui, "Nebelkind · Automatische Updates",
                           "Bitte in Kodi 'Updates automatisch installieren' aktivieren.", 9000)
                    state["last_mode_warning"] = signature
                    _save_state(state_path, state)
            elif mode is None:
                _log(xbmc, "Kodi auto-update setting could not be read", True)
            # Respect the user's global Kodi update preference. A repo refresh
            # starts Kodi's regular auto-update process if mode == 0.
            if mode == 0:
                try:
                    playing = xbmc.Player().isPlaying()
                except Exception:
                    playing = False
                if playing:
                    _log(xbmc, "Playing media: defer forced repo refresh")
                else:
                    xbmc.executebuiltin("UpdateAddonRepos")
                    _wait_for_native_install(
                        xbmc, xbmcaddon, xbmcgui, monitor,
                        pending, state, state_path)
            elif mode == 1:
                # Kodi may display the updates, but must not auto install them.
                xbmc.executebuiltin("UpdateAddonRepos")
            if monitor.waitForAbort(20):
                return
        except Exception as exc:
            _log(xbmc, "Update monitor error: " + type(exc).__name__, True)
            # Connection failure or corrupt manifest must not block Kodi.
            state["last_check"] = int(time.time())
            try:
                _save_state(state_path, state)
            except Exception:
                pass
            if monitor.waitForAbort(40):
                return


def _check_repository_installed(xbmcaddon):
    try:
        addon = xbmcaddon.Addon(id="repository.nebelkind")
        return {addon.getAddonInfo("id")}
    except Exception:
        return set()
