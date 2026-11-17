# -*- coding: utf-8 -*-
"""NebelFlix built-in auto-installer, owned by the SKIN and its widget lifecycle.

No update check or install is allowed during initial cache creation/reset.
All ZIP installs use Kodi's native repository installer. Progress represents
only versions confirmed as installed by Kodi (never fake download percent).
"""
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

MANIFEST = ("https://raw.githubusercontent.com/schlimmtendo/nebelkind.repo/"
            "main/packages/release.json")
REPO = "repository.nebelkind"
SKIN = "skin.nebelflix"
CORE = ("plugin.video.nebelflix.hybrid", "plugin.video.nebelflix.tv")
ORDER = CORE + ("plugin.video.nebelkindmusic", SKIN)
NAMES = {SKIN: "NebelFlix Skin",
         CORE[0]: "Nebelkind Hybrid",
         CORE[1]: "NebelFlix TV",
         "plugin.video.nebelkindmusic": "Nebelkind Music"}
VERSION = re.compile(r"^[0-9][A-Za-z0-9._+-]{0,47}$")
INTERVAL = 30 * 60
RETRY = 60 * 60
_WORKER = None
_LOCK = threading.Lock()


def _profile():
    import xbmcvfs
    return Path(xbmcvfs.translatePath("special://profile/addon_data/skin.nebelflix/"))


def _read(filename, default=None):
    if default is None:
        default = {}
    try:
        item = json.loads(filename.read_text(encoding="utf-8"))
        return item if isinstance(item, dict) else default
    except (OSError, ValueError, TypeError):
        return default


def _write(filename, item):
    filename.parent.mkdir(parents=True, exist_ok=True)
    temp = filename.with_name(filename.name + ".tmp")
    temp.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(temp), str(filename))


def _key(value):
    return tuple((1, int(part)) if part.isdigit() else (0, part.lower())
                 for part in re.split(r"[._-]", str(value)) if part)


def _manifest():
    request = Request(MANIFEST, headers={
        "User-Agent": "NebelFlix-Skin-AutoInstaller/1.0",
        "Cache-Control": "no-cache"})
    with urlopen(request, timeout=8) as response:
        data = response.read(131073)
    if len(data) > 131072:
        raise ValueError("release.json too large")
    item = json.loads(data.decode("utf-8"))
    if not isinstance(item, dict) or item.get("schema") != 1:
        raise ValueError("invalid Nebelkind release schema")
    rows = item.get("addons")
    if not isinstance(rows, list):
        raise ValueError("missing release list")
    versions = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        aid = row.get("id")
        ver = row.get("version")
        if aid in ORDER and isinstance(ver, str) and VERSION.fullmatch(ver):
            if aid in versions:
                raise ValueError("duplicate Nebelkind release ID")
            versions[aid] = ver
    return versions


def _installed():
    import xbmcaddon
    result = {}
    for aid in ORDER:
        try:
            result[aid] = xbmcaddon.Addon(id=aid).getAddonInfo("version")
        except Exception:
            pass
    return result


def _needed(published, installed):
    return {aid: published[aid] for aid in ORDER
            if aid in published and
            ((aid in installed and _key(published[aid]) > _key(installed[aid]))
             or (aid in CORE and aid not in installed))}


def _log(message, warning=False):
    import xbmc
    xbmc.log("[NebelFlix AutoInstaller] " + str(message),
             xbmc.LOGWARNING if warning else xbmc.LOGINFO)


def _cache_ready(profile=None, pid=None, prepare_flag=None):
    """Hard gate: a successful, persistent complete cache AND a new process."""
    if profile is None:
        profile = _profile()
    if pid is None:
        pid = os.getpid()
    if prepare_flag is None:
        import xbmcgui
        prepare_flag = xbmcgui.Window(10000).getProperty("NF.PrepareWidgets")
    if prepare_flag == "true":
        return False
    if (profile / "widget-cache-reset-request.json").exists():
        return False
    preload = _read(profile / "widget-preload-state.json")
    if not preload.get("completed_at") or preload.get("failed", 0) != 0:
        return False
    artwork = _read(profile / "artwork-preload-v1.json")
    if artwork.get("completed") is not True:
        return False
    cache = _read(profile / "widget-cache.json")
    try:
        planned = int(preload.get("planned") or 0)
    except (TypeError, ValueError):
        return False
    # A saved but empty/corrupt cache is NOT a finished 100-row preload.
    # Only count actual widget source entries: a stale cached row still counts
    # as present, unlike a missing snapshot.
    if planned <= 0 or len(cache) < max(1, (planned * 4 + 4) // 5):
        return False
    gate = _read(profile / "auto-installer-boot-gate.json")
    if gate.get("awaiting_restart") and gate.get("prepared_pid") == pid:
        return False
    return True


def mark_preload_complete():
    """Controller calls this ONLY after successful full preparation and flush.

    Even if Kodi's Quit is delayed, the current process can never install
    anything after its first preload.
    """
    path = _profile() / "auto-installer-boot-gate.json"
    _write(path, {"awaiting_restart": True,
                  "prepared_pid": os.getpid(),
                  "prepared_at": time.time()})
    _log("Initial widget cache saved; auto installer locked until new Kodi process")


def _toast(headline, message, duration=7000):
    import xbmcgui
    xbmcgui.Dialog().notification(headline, message,
                                  xbmcgui.NOTIFICATION_INFO, duration, False)


def _safe_to_install():
    import xbmc
    if not _cache_ready():
        return False
    try:
        if xbmc.Player().isPlaying():
            return False
    except Exception:
        return False
    return True


def _pause_widget_work(monitor, dialog, timeout=60):
    """Acknowledge controller pause and wait for all active plugin requests to drain.

    A modal GUI prevents users from launching another video while waiting.
    IMPORTANT: Updating an addon while Files.GetDirectory still uses it causes
    a temporary Unknown addon id error, even when installation itself succeeds.
    """
    import xbmcgui
    window = xbmcgui.Window(10000)
    window.clearProperty("NF.UpdatePauseReady")
    window.setProperty("NF.UpdatePause", "true")
    start = time.monotonic()
    quiet_since = None
    while time.monotonic() - start < timeout:
        if monitor.waitForAbort(0.5):
            return False
        if not _safe_to_install():
            _log("Cache or playback became active while preparing update", True)
            return False
        if window.getProperty("NF.UpdatePauseReady") == "true":
            if quiet_since is None:
                quiet_since = time.monotonic()
            if time.monotonic() - quiet_since >= 3:
                _log("Controller paused; plugin directory calls finished, safe to install")
                return True
        else:
            quiet_since = None
        dialog.update(0, "Widget-Abfragen werden sicher beendet. Bitte warten ...")
    _log("Controller did not pause within %d seconds; deferring update" % timeout, True)
    return False


def _resume_widget_work():
    import xbmcgui
    window = xbmcgui.Window(10000)
    window.clearProperty("NF.UpdatePause")
    window.clearProperty("NF.UpdatePauseReady")


def _verify_idle_before_exit():
    """Do not terminate the interpreter that owns the updater thread.

    A Kodi Skin controller script owns the installer Python thread. Stopping
    that controller before requesting Quit could destroy the updater itself.
    Keep the controller paused and let Kodi's normal shutdown stop it.
    """
    import xbmcgui
    window = xbmcgui.Window(10000)
    return (window.getProperty("NF.UpdatePause") == "true" and
            window.getProperty("NF.UpdatePauseReady") == "true")


def _kodi_auto_update_mode():
    """Query Kodi's REAL auto-update setting; never alter global user settings.

    Kodi Omega: addons.updatemode (0=automatic, 1=notify, 2=never).
    Unknown/unavailable setting is treated as unconfirmed, not 'disabled'.
    """
    import xbmc
    try:
        command = json.dumps({
            "jsonrpc": "2.0", "id": "nebelkind-updates",
            "method": "Settings.GetSettingValue",
            "params": {"setting": "addons.updatemode"}})
        data = json.loads(xbmc.executeJSONRPC(command))
        result = data.get("result")
        if isinstance(result, dict):
            value = result.get("value")
            if type(value) is int and value in (0, 1, 2):
                return value
    except (ValueError, TypeError, AttributeError, RuntimeError) as exc:
        _log("Unable to read Kodi addons.updatemode: %s" % exc, True)
    return None


def _ensure_automatic_updates():
    """Honor the user's request for automatic updates, not notification-only.

    Mode 1 is 'notify only' even when Kodi still checks repositories. Convert
    it to auto exactly once through Kodi's public settings API, then read it
    back. Never override mode 2 ('never check for updates'). This affects Kodi's
    global add-on update preference, not just NebelFlix.
    """
    mode = _kodi_auto_update_mode()
    if mode == 0:
        return True
    if mode != 1:
        return False
    import xbmc
    try:
        request = json.dumps({
            "jsonrpc": "2.0", "id": "nebelkind-enable-auto-updates",
            "method": "Settings.SetSettingValue",
            "params": {"setting": "addons.updatemode", "value": 0}})
        response = json.loads(xbmc.executeJSONRPC(request))
        if response.get("result") is not True:
            _log("Kodi refused to enable automatic add-on updates: %s" %
                 str(response.get("error", response))[:160], True)
            return False
        if _kodi_auto_update_mode() != 0:
            _log("Kodi did not persist the automatic update mode", True)
            return False
        _log("Changed Kodi addon update mode from notify-only (1) to auto (0)")
        return True
    except (ValueError, TypeError, AttributeError, RuntimeError) as exc:
        _log("Could not enable automatic addon updates: %s" % exc, True)
        return False

def _run_install(updates, state, state_file, monitor):
    """Native Kodi repository refresh + bounded observation.

    Kodi's updater installs available GitHub-hosted repository ZIPs when the
    automatic mode is active. No direct overlay of the running skin.
    """
    import xbmc
    import xbmcgui
    if not _safe_to_install():
        return False
    ordered = [aid for aid in ORDER if aid in updates]
    if not ordered:
        return False
    mode = _kodi_auto_update_mode()
    _log("Native addon update mode: %s (0=auto, 1=notify, 2=never)" % mode)
    if mode != 0:
        if not _ensure_automatic_updates():
            _log("Automatic installation blocked by Kodi addon update setting", True)
            _toast("NebelFlix · Update ausstehend",
                   "Kodi kann Updates nicht automatisch installieren. Bitte Update-Modus prüfen.", 9000)
            return False
    dialog = xbmcgui.DialogProgress()
    pause_requested = False
    successful = False
    signature = ";".join(aid + "=" + updates[aid] for aid in ordered)
    try:
        dialog.create("NebelFlix · Auto-Installer",
                      "Updates gefunden. Kodi bereitet die Installation vor ...")
        pause_requested = True
        # Before changing any addon, stop scheduling new plugin:// directory calls.
        if not _pause_widget_work(monitor, dialog, timeout=25):
            _log("Native update deferred: widget workers are not idle", True)
            return False
        if not _safe_to_install():
            return False
        state["active"] = {
            "targets": dict(updates), "pid": os.getpid(),
            "started": time.time(), "signature": signature,
            "mechanism": "native-kodi-repository"}
        _write(state_file, state)
        dialog.update(0, "Kodi sucht im Repository nach Updates ...")
        _log("Requested native Kodi repository refresh for %s" % signature)
        # IMPORTANT: This is the ONLY Kodi builtin that triggers our update.
        # In auto mode, Kodi's repository updater itself installs new versions.
        xbmc.executebuiltin("UpdateAddonRepos")
        completed = set()
        start = time.monotonic()
        limit = 70
        while time.monotonic() - start < limit:
            if monitor.waitForAbort(2):
                return False
            if not _safe_to_install():
                _log("Kodi is busy or cache was reset; no further installer actions", True)
                return False
            if not _verify_idle_before_exit():
                _log("Skin lost widget pause while Kodi refreshed its repository", True)
                return False
            current = _installed()
            completed = {aid for aid in ordered
                         if aid in current and _key(current[aid]) >= _key(updates[aid])}
            percent = 90 * len(completed) // len(ordered)
            remaining = max(0, limit - int(time.monotonic() - start))
            dialog.update(percent, "%d von %d Aktualisierungen bestätigt. Kodi arbeitet ... (%ds)" %
                          (len(completed), len(ordered), remaining))
            if len(completed) == len(ordered):
                break
        if len(completed) != len(ordered):
            pending = [NAMES[aid] for aid in ordered if aid not in completed]
            _log("Native Kodi updater timed out, not installed: " + ", ".join(pending), True)
            dialog.update(90 * len(completed) // len(ordered),
                          "Update ausstehend. Kodi bleibt nutzbar; später erneut versuchen.")
            monitor.waitForAbort(2)
            return False
        dialog.update(95, "Alle Updates sind installiert. Kodi wird beendet ...")
        _log("Confirmed all native Kodi updates: " + signature)
        if not _verify_idle_before_exit():
            _log("Controller not paused at install completion; aborting automatic quit", True)
            return False
        # Kodi handles its own addon install; close only when ALL versions match.
        state["active"]["completed"] = True
        _write(state_file, state)
        dialog.update(100, "Installation abgeschlossen. Kodi wird beendet ...")
        _log("Native Kodi updates confirmed, requesting clean Quit")
        successful = True
        if not monitor.abortRequested():
            xbmc.executebuiltin("Quit")
            if not monitor.waitForAbort(8):
                _log("Kodi exit not acknowledged; releasing UI and requiring manual restart", True)
                successful = False
                _toast("NebelFlix · Neustart erforderlich",
                       "Updates sind installiert. Kodi bitte selbst neu starten.", 8500)
                return False
        return True
    finally:
        # A timeout or Kodi error must NEVER leave a modal or widget pause stuck.
        if pause_requested and not successful:
            try:
                _resume_widget_work()
            except Exception as exc:
                _log("Could not clear widget pause flag: %s" % exc, True)
            if isinstance(state.get("active"), dict):
                state.pop("active", None)
                try:
                    _write(state_file, state)
                except OSError as exc:
                    _log("Could not clear active update state: %s" % exc, True)
        try:
            dialog.close()
        except Exception:
            pass


def _check_due(last_check, now, first_check_in_session=False):
    """Each new Kodi process checks immediately, then every 30 minutes."""
    if first_check_in_session:
        return True
    try:
        last = int(last_check or 0)
    except (ValueError, TypeError):
        return True
    return now - last >= INTERVAL


def _loop():
    import xbmc
    import xbmcaddon
    monitor = xbmc.Monitor()
    state_file = _profile() / "auto-installer-state.json"
    if monitor.waitForAbort(2):
        return
    first_check_in_session = True
    while not monitor.abortRequested():
        try:
            if not _safe_to_install():
                if monitor.waitForAbort(30):
                    return
                continue
            try:
                xbmcaddon.Addon(id=REPO)
            except Exception:
                _log("Repository not installed; automatic updates unavailable", True)
                if monitor.waitForAbort(120):
                    return
                continue
            state = _read(state_file)
            installed = _installed()
            # Never force-quit Kodi because a previous process left a
            # completed/stale marker behind. That used to trap users in a loop.
            active = state.pop("active", None)
            if isinstance(active, dict):
                targets = active.get("targets", {})
                if isinstance(targets, dict):
                    still_pending = {aid: ver for aid, ver in targets.items()
                                     if aid not in installed or
                                     _key(installed[aid]) < _key(ver)}
                    if still_pending:
                        state["last_failed_signature"] = active.get("signature", "")
                        state["last_attempt"] = int(time.time())
                        _log("Recovered interrupted update without forcing Kodi exit: %s" %
                             json.dumps(still_pending), True)
                    else:
                        _log("Previous update finished; cleared stale update state")
                _write(state_file, state)
            now = int(time.time())
            if not _check_due(state.get("last_check"), now, first_check_in_session):
                if monitor.waitForAbort(30):
                    return
                continue
            published = _manifest()
            first_check_in_session = False
            updates = _needed(published, installed)
            _log("Startup release check: installed=%s; online=%s; pending=%s" %
                 (json.dumps(installed, sort_keys=True),
                  json.dumps(published, sort_keys=True),
                  json.dumps(updates, sort_keys=True)))
            state["last_check"] = now
            _write(state_file, state)
            if updates:
                signature = ";".join(aid + "=" + updates[aid] for aid in ORDER if aid in updates)
                if signature != state.get("last_failed_signature") or now - int(state.get("last_attempt", 0)) >= RETRY:
                    if _safe_to_install():
                        result = _run_install(updates, state, state_file, monitor)
                        if not result:
                            state["last_failed_signature"] = signature
                            state["last_attempt"] = int(time.time())
                            state["last_check"] = now - INTERVAL + RETRY
                            _write(state_file, state)
            if monitor.waitForAbort(30):
                return
        except Exception as exc:
            _log("Auto-installer check failed (%s): %s" %
                 (type(exc).__name__, str(exc)[:200]), True)
            if monitor.waitForAbort(90):
                return


def start_if_ready():
    """Called only by the NebelFlix Home controller, never a standalone add-on."""
    global _WORKER
    if not _cache_ready():
        return False
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return True
        _WORKER = threading.Thread(target=_loop, name="NebelFlixAutoInstaller",
                                   daemon=True)
        _WORKER.start()
    return True
