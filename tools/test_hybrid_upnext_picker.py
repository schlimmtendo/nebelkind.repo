#!/usr/bin/env python3
"""Offline regression tests for NebelFlix Hybrid Up Next manual source picker."""
import ast
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
ADDON = "plugin.video.nebelflix.hybrid"
VERSION = json.loads((ROOT / "release_versions.json").read_text(encoding="utf-8"))["plugin.video.nebelflix.hybrid"]
NEXT = ADDON + "/resources/lib/next_episode.py"
PLAYER = ADDON + "/resources/lib/player.py"
SOURCES = ADDON + "/resources/lib/sources.py"
PROGRESS = ADDON + "/resources/lib/source_progress.py"
PICKER = ADDON + "/resources/lib/source_list_window.py"


def compiled_definition(source, wanted, scope, kind=ast.FunctionDef):
    module = ast.parse(source)
    func = next((n for n in module.body if isinstance(n, kind) and n.name == wanted),
                None)
    assert func is not None, wanted
    tree = ast.fix_missing_locations(ast.Module(body=[func], type_ignores=[]))
    exec(compile(tree, "<up-next-test>", "exec"), scope)
    return scope[wanted]


def test_confirmed_episode(source):
    """TMDb handoff URL must select Episode 2 but never choose its host."""
    episode = ast.parse(source)
    scope = {"hashlib": hashlib, "date": date, "urlencode": urlencode,
             "json": json}
    for func in episode.body:
        if isinstance(func, ast.FunctionDef) and func.name in (
                "_number", "_stable_id", "_aired", "_episode_in_season",
                "next_episode"):
            body = ast.fix_missing_locations(ast.Module(body=[func], type_ignores=[]))
            exec(compile(body, "<next_episode>", "exec"), scope)

    class FakeTMDb:
        def get_meta_seasons(self, show_id, season, advanced="true"):
            assert show_id == 123 and season == 1
            assert advanced == "true"
            aired = (date.today() - timedelta(days=2)).isoformat()
            return {"episodes": [
                {"id": 12, "episode_number": 1, "air_date": aired, "name": "Erste Folge"},
                {"id": 13, "episode_number": 2, "air_date": aired, "name": "Zweite Folge",
                 "overview": "Folge 2", "still_path": "/still.jpg"}]}
    scope["cTMDB"] = FakeTMDb

    data = {"mediatype": "tvshow", "tmdb_id": 123, "season": 1,
            "episode": 1, "number_of_seasons": 1, "title": "Beispielserie",
            "fanart": "https://example.org/serie-fanart.jpg",
            "clearlogo": "https://example.org/serie-logo.png",
            "poster": "https://example.org/poster.jpg"}
    result = scope["next_episode"](data)
    assert result is not None
    query = parse_qs(urlsplit(result["play_url"]).query)
    assert query["action"] == ["play"]
    metadata = json.loads(query["sysmeta"][0])
    assert metadata["season"] == 1 and metadata["episode"] == 2
    assert metadata["select"] == "1", "TMDb path still forces autoplay"
    assert "_series_next_autoplay" not in metadata, "Small BG dialog still forced"
    assert metadata["fanart"] == data["fanart"]
    assert metadata["clearlogo"] == data["clearlogo"]
    assert result["next_episode"]["episode"] == 2
    if "_autoplay_queue" in source:
        automatic = scope["next_episode"](dict(data, _autoplay_queue=True))
        auto_meta = json.loads(parse_qs(urlsplit(automatic["play_url"]).query)["sysmeta"][0])
        assert auto_meta["select"] == "2"
        assert auto_meta["_autoplay_queue"] is True


def test_player_fallback(source):
    """Fallback for delayed TMDb must open the same source picker."""
    tree = ast.parse(source)
    player_cls = next((n for n in tree.body
                       if isinstance(n, ast.ClassDef) and n.name == "player"), None)
    assert player_cls is not None
    method = next((n for n in player_cls.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_next_episode_meta"),
                  None)
    assert method is not None
    wrapper = ast.ClassDef(name="PlaybackFake", bases=[], keywords=[], body=[method],
                           decorator_list=[])
    ns = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
                 "<player-upnext>", "exec"), ns)
    subject = ns["PlaybackFake"]()
    subject.meta = {"title": "Beispielserie", "season": 1, "episode": 1,
                    "mediatype": "tvshow", "_series_next_autoplay": True,
                    "select": "2"}
    subject.season, subject.episode = 1, 1
    subject.number_of_episodes, subject.number_of_seasons = 6, 2
    next_data = subject._next_episode_meta()
    assert next_data["episode"] == 2
    assert next_data["select"] == "1", "Fallback still forces autoplay"
    assert "_series_next_autoplay" not in next_data, "Inherited BG dialog flag"
    assert subject.meta["select"] == "2", "Original episode mutated"
    subject.episode, subject.number_of_episodes = 6, 6
    next_season = subject._next_episode_meta()
    assert (next_season["season"], next_season["episode"]) == (2, 1)
    assert next_season["select"] == "1"
    assert "number_of_episodes" not in next_season
    if "_autoplay_queue" in source:
        subject.meta["_autoplay_queue"] = True
        assert subject._next_episode_meta()["select"] == "2"


def main():
    plan = json.loads((ROOT / "release_versions.json").read_text(encoding="utf-8"))
    if plan.get(ADDON) != VERSION:
        print("Hybrid Up Next regression skipped; this is not its release")
        return
    target = ROOT / "packages" / ADDON / (ADDON + "-" + VERSION + ".zip")
    with ZipFile(target) as archive:
        assert archive.testzip() is None
        src = {name: archive.read(name).decode("utf-8")
               for name in (NEXT, PLAYER, SOURCES, PROGRESS, PICKER)}
        for filename, text in src.items():
            compile(text, filename, "exec")
        test_confirmed_episode(src[NEXT])
        test_player_fallback(src[PLAYER])
        assert "else _NextProgress() if self._series_next_autoplay" in src[SOURCES] or "progressDialog = (_NextProgress() if self._series_next_autoplay" in src[SOURCES]
        assert "else SourceProgress(json.loads(self.sysmeta)))" in src[SOURCES]
        assert "elif select == '1':" in src[SOURCES]
        assert "picked = choose_source(items, meta)" in src[SOURCES]
        assert "if picked < 0:" in src[SOURCES]
        assert "def choose_source(" in src[PICKER]
        assert "self.getControl(10).setImage(self.meta.get('fanart')" in src[PROGRESS]
        assert "self.getControl(11).setImage(initial)" in src[PROGRESS]
        assert "self._on_next_now" in src[PLAYER]
        assert "def _offer_next_episode(self):" in src[PLAYER]
        assert "series.next.autoplay" in src[PLAYER]
        assert "RunPlugin(%s)" in src[PLAYER]
        assert "NEBELFLIX_UPNEXT_MANUAL_SOURCES_V1" in src[PLAYER] or "_autoplay_queue" in src[PLAYER]
    print("PASS: TMDb and fallback handoffs, full-screen fanart/logo progress,"
          " manual source selection, cancel and Up Next triggers")


if __name__ == "__main__":
    main()
