#!/usr/bin/env python3
"""Wire the Skin-owned auto-installer into the existing Kodi preload lifecycle.

Patch only the new selected Skin ZIP. Refuse ambiguous / unexpected code.
Keep all unaffected ZIP members and byte payloads unchanged.
"""
import json
import os
from pathlib import Path
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
SKIN = "skin.nebelflix"
SCRIPT = SKIN + "/resources/controller.py"
MARK = "# NEBELFLIX_SKIN_AUTOINSTALLER_V1"
PAUSE_MARK = "# NEBELFLIX_INSTALL_PAUSE_V2"


def patch(content):
    # The shipped Windows-authored controller uses CRLF. Match anchors
    # against LF consistently and emit a valid UTF-8 Python module.
    content = content.replace("\r\n", "\n")
    if MARK in content:
        return content
    replacements = [
        (
            "    pending_reset = library.read(library.WIDGET_RESET_MARKER, {})\n",
            "    pending_reset = library.read(library.WIDGET_RESET_MARKER, {})\n"
            "    first_full_setup = (not bool(library.read(library.PRELOAD_STATE, {}).get('completed_at'))\n"
            "                        or bool(pending_reset))\n"
            "    boot_gate_saved = False\n"
        ),
        (
            "    prop('BackgroundCacheNotice', 'false')\n"
            "    if prepare_started is None and planned - ready >= max(12, int(planned * 0.35)):\n",
            "    prop('BackgroundCacheNotice', 'false')\n"
            "    # " + MARK + "  No updater launch during the first widget preload.\n"
            "    if prepare_started is None:\n"
            "        try:\n"
            "            from auto_installer import start_if_ready\n"
            "            if start_if_ready():\n"
            "                log('Skin AutoInstaller running after cache-ready boot')\n"
            "            else:\n"
            "                log('Skin AutoInstaller blocked: cache not ready or same first boot')\n"
            "        except Exception as exc:\n"
            "            log('Skin AutoInstaller deferred: %s' % exc, xbmc.LOGWARNING)\n"
            "    if prepare_started is None and planned - ready >= max(12, int(planned * 0.35)):\n"
        ),
        (
            "                        library.write('artwork-preload-v1.json', {'completed': True, 'failed_images': widgets.artwork.failures})\n",
            "                        library.write('artwork-preload-v1.json', {'completed': True, 'failed_images': widgets.artwork.failures})\n"
            "                        if first_full_setup and errors == 0:\n"
            "                            try:\n"
            "                                from auto_installer import mark_preload_complete\n"
            "                                mark_preload_complete()\n"
            "                                boot_gate_saved = True\n"
            "                            except Exception as exc:\n"
            "                                log('Cannot persist AutoInstaller restart gate: %s' % exc, xbmc.LOGWARNING)\n"
        ),
        (
            "if done >= total and errors == 0 and not stalled and not skipped and not artwork_upgrade:",
            "if done >= total and errors == 0 and not stalled and not skipped and first_full_setup and boot_gate_saved:"
        ),
        (
            "                        xbmcgui.Dialog().ok(\n"
            "                            'Nebelflix – Vorbereitung abgeschlossen',\n"
            "                            'Alle Widget-Reihen sind vorbereitet und gespeichert. '\n"
            "                            'Kodi wird jetzt beendet. Bitte starte Kodi danach manuell neu.')",
            "                        xbmcgui.Dialog().notification(\n"
            "                            'NebelFlix – Vorbereitung abgeschlossen',\n"
            "                            'Widget-Cache gespeichert. Kodi wird beendet; bitte danach neu starten.',\n"
            "                            xbmcgui.NOTIFICATION_INFO, 6500)\n"
            "                        MONITOR.waitForAbort(2)"
        )
    ]
    for old, new in replacements:
        count = content.count(old)
        if count != 1:
            raise ValueError("Unsafe Skin controller patch: expected one anchor, got %d: %s" %
                             (count, old[:100]))
        content = content.replace(old, new, 1)
    compile(content, SCRIPT, "exec")
    return content

def patch_pause(content):
    """Suspend new widget work while queued addon updates are applied."""
    if PAUSE_MARK in content:
        return content
    anchor = ("            if prop('StopController') == 'true':\\n"
              "                break\\n")
    if content.count(anchor) != 1:
        raise ValueError('Unsafe pause gate: unique stop-controller anchor missing')
    gate = (
        "            if prop('StopController') == 'true':\\n"
        "                break\\n"
        "            " + PAUSE_MARK + "\\n"
        "            if prop('UpdatePause') == 'true':\\n"
        "                # Don't schedule new widget requests while Kodi replaces an addon.\\n"
        "                # Worker threads remove completed RPCs under the lock.\\n"
        "                try:\\n"
        "                    with widgets.lock:\\n"
        "                        inflight = len(widgets.inflight)\\n"
        "                    prop('UpdatePauseReady', 'true' if inflight == 0 else 'false')\\n"
        "                except Exception as exc:\\n"
        "                    prop('UpdatePauseReady', 'false')\\n"
        "                    log('Cannot confirm idle widget requests: %s' % exc, xbmc.LOGWARNING)\\n"
        "                continue\\n"
        "            if prop('UpdatePauseReady') == 'true':\\n"
        "                prop('UpdatePauseReady', 'false')\\n"
    )
    content = content.replace(anchor, gate, 1)
    compile(content, SCRIPT, 'exec')
    return content


def main():
    v = json.loads((ROOT / "release_versions.json").read_text(encoding="utf-8"))
    version = v.get(SKIN)
    if not version:
        print("No Skin version in release plan; leave current package unchanged")
        return
    path = PACKAGES / SKIN / (SKIN + "-" + version + ".zip")
    if not path.is_file():
        raise ValueError("New Skin ZIP not available to patch: " + str(path))
    with ZipFile(path) as original:
        members = original.infolist()
        old = original.read(SCRIPT)
        new = patch_pause(patch(old.decode("utf-8"))).encode("utf-8")
        if new == old:
            print("Skin controller already has integrated updater:", path.name)
            return
        handle, temp = tempfile.mkstemp(prefix="nebinst-", suffix=".zip", dir=path.parent)
        os.close(handle)
        try:
            with ZipFile(temp, "w") as target:
                for item in members:
                    target.writestr(item, new if item.filename == SCRIPT else original.read(item.filename))
            with ZipFile(temp) as check:
                if check.testzip() is not None:
                    raise ValueError("Skin controller ZIP failed integrity verification")
                if MARK not in check.read(SCRIPT).decode("utf-8") or PAUSE_MARK not in check.read(SCRIPT).decode("utf-8"):
                    raise ValueError("New controller missing update gate")
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    print("Installed safe widget-preload updater gate in", path.name)


if __name__ == "__main__":
    main()
