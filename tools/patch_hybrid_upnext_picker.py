#!/usr/bin/env python3
"""Up Next in NebelFlix Hybrid: normal source search + manual source selection.

This changes ONLY the Up Next handoff metadata in next_episode.py and player.py.
It does not alter scraper providers, source ranking, ordinary playback modes,
the Up Next 90% prompt, the skin, or persistent user preferences.
The already existing SourceProgress and choose_source paths are reused.
"""
import json
import os
from pathlib import Path
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
ADDON = "plugin.video.nebelflix.hybrid"
VERSION = json.loads((ROOT / "release_versions.json").read_text(encoding="utf-8"))["plugin.video.nebelflix.hybrid"]
NEXT = ADDON + "/resources/lib/next_episode.py"
PLAYER = ADDON + "/resources/lib/player.py"


def replace_once(source, original, updated, label):
    found = source.count(original)
    if found != 1:
        raise ValueError("%s changed upstream (expected once; found %d)" % (label, found))
    return source.replace(original, updated, 1)


def patch_next_episode(source):
    """Preserve explicit user Autoplay; keep compatibility with older releases."""
    if "_autoplay_queue" in source:
        return source
    if "playcount=0, select='1'," in source and "_series_next_autoplay=True," not in source:
        return source
    source = replace_once(
        source, "number_of_episodes=len(rows), playcount=0, select='2',",
        "number_of_episodes=len(rows), playcount=0, select='1',",
        "TMDb next-episode source picker")
    source = replace_once(
        source, "                     _series_next_autoplay=True,\n", "",
        "TMDb next-episode background-dialog flag")
    compile(source, NEXT, "exec")
    return source


def patch_player(source):
    """The bounded fallback successor must use the identical normal picker."""
    if "_autoplay_queue" in source:
        return source
    mark = "# NEBELFLIX_UPNEXT_MANUAL_SOURCES_V1"
    if mark in source:
        return source
    original = """            # Nur der automatische Folgewechsel nutzt die direkte Streamwahl.
            # Normale Episodenaufrufe und Filme behalten die konfigurierte Hosterliste.
            meta['select'] = '2'
            meta['_series_next_autoplay'] = True"""
    updated = """            # NEBELFLIX_UPNEXT_MANUAL_SOURCES_V1
            # Auto-select the EPISODE, never the stream: use the ordinary
            # full-screen SourceProgress and the normal source picker.
            meta['select'] = '1'
            # Clear stale flags when this episode originated in an older build.
            meta.pop('_series_next_autoplay', None)"""
    source = replace_once(source, original, updated,
                          "fallback next-episode picker")
    compile(source, PLAYER, "exec")
    return source


def main():
    plan = json.loads((ROOT / "release_versions.json").read_text(encoding="utf-8"))
    if plan.get(ADDON) != VERSION:
        print("Up Next picker release not scheduled")
        return
    archive_path = ROOT / "packages" / ADDON / (ADDON + "-" + VERSION + ".zip")
    if not archive_path.is_file():
        raise ValueError("No staged Hybrid ZIP: " + str(archive_path))
    with ZipFile(archive_path, "r") as source_zip:
        if source_zip.testzip() is not None:
            raise ValueError("Source ZIP corrupt")
        changes = {
            NEXT: patch_next_episode(source_zip.read(NEXT).decode("utf-8")).encode("utf-8"),
            PLAYER: patch_player(source_zip.read(PLAYER).decode("utf-8")).encode("utf-8"),
        }
        fd, temp = tempfile.mkstemp(prefix="neb-upnext-picker-", suffix=".zip",
                                    dir=archive_path.parent)
        os.close(fd)
        try:
            with ZipFile(temp, "w") as target_zip:
                for member in source_zip.infolist():
                    if member.filename in changes:
                        target_zip.writestr(member, changes[member.filename])
                    else:
                        target_zip.writestr(member, source_zip.read(member.filename))
            with ZipFile(temp, "r") as verify_zip:
                if verify_zip.testzip() is not None:
                    raise ValueError("Up Next ZIP failed integrity test")
                if (b"NEBELFLIX_UPNEXT_MANUAL_SOURCES_V1" not in verify_zip.read(PLAYER)
                        and b"_autoplay_queue" not in verify_zip.read(PLAYER)):
                    raise ValueError("Up Next patch missing")
            source_zip.close()  # Windows: release original archive before replacing it.
            os.replace(temp, archive_path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    print("Updated Up Next TMDb and fallback handoffs to normal source selection:", VERSION)


if __name__ == "__main__":
    main()
