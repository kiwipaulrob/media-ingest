"""Ingest must not create a second copy of something the library already has.

A finished torrent is not a reason to ingest. The gate asks quality.decide() whether this
download beats what is held: place when nothing comparable exists or the copy is measurably
better, refuse when the held copy is equal or better, and REVIEW when the held copy cannot
be measured — uncertainty must never resolve to "ingest another copy".
"""
import json

from cli import ingest_new as cli_ingest
from media_lib import jellyfin as jf, place as placemod, qb as qbmod
from media_lib import quality as qualitymod, safety as safetymod

DEST = "/mnt/zmedia/videos/musicdocos/White Riot (2019)/White Riot (2019).mkv"


def _run(tmp_path, monkeypatch, verdict):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps([{"src": "/mnt/zmedia/downloads/White.Riot.1080p/x.mkv",
                                 "dest": DEST}]))
    placed = []
    monkeypatch.setattr(cli_ingest, "config", type("C", (), {
        "ensure_state_dir": staticmethod(lambda: None), "STATE_DIR": tmp_path})())
    monkeypatch.setattr(qbmod, "torrent_for_path", lambda p: None)
    monkeypatch.setattr(qualitymod, "decide", lambda cand, term, **k: {"verdict": verdict})
    monkeypatch.setattr(placemod, "place_new",
                        lambda *a, **k: placed.append(a) or {"ok": True, "stage": "PLACED"})
    monkeypatch.setattr(safetymod, "record", lambda **kw: None)
    monkeypatch.setattr(jf, "notify_media", lambda paths: 204)
    monkeypatch.setattr(jf, "refresh_library", lambda lib: 204)
    cli_ingest.main(["--spec", str(spec)])
    return placed


def test_a_worse_copy_is_not_ingested(tmp_path, monkeypatch, capsys):
    placed = _run(tmp_path, monkeypatch, "same-or-worse")
    assert placed == [], "a duplicate of a held title must never be placed"
    assert "HELD-BETTER" in capsys.readouterr().out


def test_a_better_copy_is_ingested(tmp_path, monkeypatch):
    assert len(_run(tmp_path, monkeypatch, "upgrade")) == 1


def test_a_new_title_is_ingested(tmp_path, monkeypatch):
    assert len(_run(tmp_path, monkeypatch, "no-local-copy")) == 1


def test_an_unmeasurable_held_copy_is_reviewed_not_duplicated(tmp_path, monkeypatch, capsys):
    """'Could not measure' is not 'we hold nothing'."""
    assert _run(tmp_path, monkeypatch, "held-unknown") == []
    assert "REVIEW" in capsys.readouterr().out


def test_a_placed_file_must_report_a_runtime(monkeypatch):
    """A 0.0-minute row is a real failure mode, not cosmetic: it is how items looked
    'invisible' before. ensure_row_runtime must refresh the item and return the runtime."""
    from media_lib import jellyfin as jf

    row = {"Id": "abc", "Path": "/x/film.mkv", "RunTimeTicks": 0}
    calls = {"refresh": 0}

    def fake_items(lib_id):
        if calls["refresh"]:
            row["RunTimeTicks"] = int(119.2 * 600_000_000)
        return [row]

    monkeypatch.setattr(jf, "list_items", fake_items)
    monkeypatch.setattr(jf, "refresh_item",
                        lambda i: calls.__setitem__("refresh", 1) or 204)
    monkeypatch.setattr(jf.time, "sleep", lambda s: None)

    assert jf.ensure_row_runtime("/x/film.mkv", "lib") == 119.2
    assert calls["refresh"] == 1
