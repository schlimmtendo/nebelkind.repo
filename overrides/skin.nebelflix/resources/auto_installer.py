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
RETRY = 20 * 60
WAIT_PER_ADDON = 180
_BOOT = None
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


def _stop_controller_before_exit(monitor, timeout=12):
    """Allow Skin's controller to persist its cache and close pools BEFORE Quit."""
    import xbmcgui
    window = xbmcgui.Window(10000)
    window.setProperty("NF.StopController", "true")
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if monitor.waitForAbort(0.3):
            return False
        if not window.getProperty("NF.Heartbeat"):
            _log("Skin controller stopped cleanly before Kodi exit")
            return True
    _log("Controller shutdown acknowledgement timed out", True)
    return False


def _run_install(updates, state, state_file, monitor):
    """Modal, controller-quiesced Kodi install; progress counts confirmed installs."""
    import xbmc
    import xbmcgui
    if not _safe_to_install():
        return False
    ordered = [aid for aid in ORDER if aid in updates]
    if not ordered:
        return False
    total = len(ordered)
    signature = ";".join(aid + "=" + updates[aid] for aid in ordered)
    dialog = xbmcgui.DialogProgress()  # REAL MODAL: blocks touch and remote navigation
    successful = False
    pause_requested = False
    try:
        dialog.create("NebelFlix · Auto-Installer",
                      "Neue Addon-Versionen gefunden. Bitte warten ...")
        _log("Opened modal Kodi update dialog for %d addons" % total)
        pause_requested = True
        if not _pause_widget_work(monitor, dialog):
            _log("Update deferred: widget tasks did not become idle", True)
            return False
        # No ZIP is touched until all plugin-backed widget requests are quiet.
        state["active"] = {"targets": updates, "pid": os.getpid(),
                           "started": time.time(), "signature": signature}
        _write(state_file, state)
        already = set()
        for aid in ordered:
            if monitor.abortRequested() or not _safe_to_install():
                _log("Install aborted: Kodi shut down or cache/playback guard changed", True)
                return False
            current = _installed()
            if aid in current and _key(current[aid]) >= _key(updates[aid]):
                already.add(aid)
                dialog.update((len(already) * 90) // total,
                              "%d von %d Addons abgeschlossen" % (len(already), total))
                continue
            dialog.update((len(already) * 90) // total,
                          "%s wird aktualisiert – bitte Kodi nicht bedienen" % NAMES[aid])
            _log("Request native Kodi install for %s => %s" % (aid, updates[aid]))
            xbmc.executebuiltin("InstallAddon(%s)" % aid)
            started = time.monotonic()
            while time.monotonic() - started < WAIT_PER_ADDON:
                if monitor.waitForAbort(2):
                    return False
                if not _safe_to_install():
                    _log("Cache or playback became active during Kodi install", True)
                    return False
                current = _installed()
                if aid in current and _key(current[aid]) >= _key(updates[aid]):
                    already.add(aid)
                    # 90% is the maximum until controller shutdown is confirmed.
                    dialog.update((len(already) * 90) // total,
                                  "%s aktualisiert (%d von %d)" %
                                  (NAMES[aid], len(already), total))
                    _log("Confirmed installed %s %s" % (aid, current[aid]))
                    break
            else:
                _log("Kodi could not confirm installation of " + aid, True)
                state["last_attempt"] = int(time.time())
                _write(state_file, state)
                dialog.update((len(already) * 90) // total,
                              "Update unvollständig. Bitte später erneut versuchen.")
                monitor.waitForAbort(3)
                return False
        if len(already) != total:
            return False
        # Hold the modal screen; don't let widgets resume between update and exit.
        dialog.update(95, "Updates installiert. NebelFlix wird sicher beendet ...")
        state["active"]["completed"] = True
        _write(state_file, state)
        if not _stop_controller_before_exit(monitor):
            dialog.update(95, "Warte auf das sichere Beenden des Skin-Controllers ...")
            _log("Exit deferred: controller did not finish shutdown", True)
            return False
        dialog.update(100, "Updates abgeschlossen. Kodi wird beendet ...")
        _log("Installed versions and controller shutdown confirmed; requesting Quit")
        successful = True
        if not monitor.abortRequested():
            xbmc.executebuiltin("Quit")
        return True
    finally:
        # On failure restore widgets; on success leave them paused until Kodi Quit.
        if pause_requested and not successful:
            _resume_widget_work()
            if isinstance(state.get("active"), dict):
                state.pop("active", None)
                _write(state_file, state)
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
            active = state.get("active")
            if isinstance(active, dict) and active.get("completed"):
                if active.get("pid") == os.getpid():
                    _log("Completed update in current process; Kodi should be closed")
                    xbmc.executebuiltin("Quit")
                    return
                state.pop("active", None)
                _write(state_file, state)
            elif isinstance(active, dict) and active.get("targets"):
                targets = active["targets"]
                if all(aid in installed and _key(installed[aid]) >= _key(target)
                       for aid, target in targets.items()):
                    _log("Interrupted install finished in Kodi: " + str(targets))
                    if active.get("pid") == os.getpid():
                        active["completed"] = True
                        _write(state_file, state)
                        xbmc.executebuiltin("Quit")
                        return
                    state.pop("active", None)
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
                    xbmc.executebuiltin("UpdateAddonRepos")
                    if monitor.waitForAbort(12):
                        return
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
