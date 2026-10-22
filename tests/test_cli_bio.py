"""cli.bio — one entry point for the bio work that used to live in three scripts.

The tests target the parts that decide what gets WRITTEN, because that is where a
mistake reaches the library: the status filter (suspect must never be applied),
the id gate, and the alias loop's no-row / no-prose handling.
"""
import argparse
import json

from cli import bio


def _args(**kw):
    base = {"library": "lib", "limit": None, "out": None, "plan": None,
            "ids": None, "file": None}
    base.update(kw)
    return argparse.Namespace(**base)


def _plan(tmp_path, rows):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"people_gaps": len(rows), "rows": rows}))
    return str(p)


def test_fill_never_applies_suspects(tmp_path, monkeypatch):
    """`suspect` looks ready and is not — the whole point of the status."""
    applied = []
    monkeypatch.setattr(bio.enrich, "apply_person",
                        lambda pid, text, src: applied.append(pid) or {"status": "DONE"})
    plan = _plan(tmp_path, [
        {"id": "a", "status": "ready", "bio": "x" * 100, "source": "lastfm"},
        {"id": "b", "status": "suspect", "bio": "y" * 100, "source": "lastfm"},
        {"id": "c", "status": "review-birth", "bio": "z" * 100, "source": "wikipedia"},
        {"id": "d", "status": "gap", "bio": "", "source": None},
        {"id": "e", "status": "thin", "bio": "short", "source": "wikipedia"},
    ])
    bio.cmd_fill(_args(plan=plan))
    assert applied == ["a", "e"], "only ready/thin may be written"


def test_fill_honours_explicit_ids(tmp_path, monkeypatch):
    applied = []
    monkeypatch.setattr(bio.enrich, "apply_person",
                        lambda pid, text, src: applied.append(pid) or {"status": "DONE"})
    plan = _plan(tmp_path, [
        {"id": "a", "status": "ready", "bio": "x" * 100, "source": "lastfm"},
        {"id": "b", "status": "ready", "bio": "y" * 100, "source": "lastfm"},
    ])
    bio.cmd_fill(_args(plan=plan, ids="2"))
    assert applied == ["b"], "an explicit id must not drag its neighbour in"


def test_fill_reports_nothing_to_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(bio.enrich, "apply_person", lambda *a: {"status": "DONE"})
    plan = _plan(tmp_path, [{"id": "a", "status": "gap", "bio": "", "source": None}])
    assert bio.cmd_fill(_args(plan=plan)) == 2


def test_alias_skips_a_name_with_no_person_row(tmp_path, monkeypatch, capsys):
    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps([
        {"name": "Ghost Name", "wiki_title": "Ghost Name", "band_page": False},
    ]))
    monkeypatch.setattr(bio, "_wiki_batch", lambda titles: {"Ghost Name": "A real bio. " * 8})
    monkeypatch.setattr(bio.enrich, "person_rows", lambda uid=None: {})
    bio.cmd_alias(_args(file=str(pairs)))
    out = capsys.readouterr().out
    assert "NO-ROW" in out


def test_alias_writes_prose_and_flags_band_pages(tmp_path, monkeypatch, capsys):
    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps([
        {"name": "Roger Charlery", "wiki_title": "Ranking Roger", "band_page": True},
    ]))
    monkeypatch.setattr(bio, "_wiki_batch", lambda titles: {"Ranking Roger": "Ranking Roger bio. " * 6})
    monkeypatch.setattr(bio.enrich, "person_rows", lambda uid=None: {"Roger Charlery": {"Id": "p1"}})
    written = []
    monkeypatch.setattr(bio.enrich, "apply_person",
                        lambda pid, text, src: written.append((pid, src)) or {"status": "DONE"})
    bio.cmd_alias(_args(file=str(pairs)))
    assert written == [("p1", "wikipedia")]
    assert "band page" in capsys.readouterr().out


def test_alias_reports_missing_prose_without_writing(tmp_path, monkeypatch, capsys):
    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps([{"name": "X", "wiki_title": "X", "band_page": False}]))
    monkeypatch.setattr(bio, "_wiki_batch", lambda titles: {})
    monkeypatch.setattr(bio.enrich, "person_rows", lambda uid=None: {"X": {"Id": "p1"}})
    wrote = []
    monkeypatch.setattr(bio.enrich, "apply_person",
                        lambda *a: wrote.append(a) or {"status": "DONE"})
    bio.cmd_alias(_args(file=str(pairs)))
    assert wrote == [], "no prose means no write"
    assert "GAP-NO-PROSE" in capsys.readouterr().out


def test_worklist_skips_no_row_names_in_the_listing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bio.enrich, "build_plan", lambda *a, **kw: {
        "people_gaps": 2, "ready": 1, "thin": 0, "suspect": 0, "gap": 0, "no_row": 1,
        "rows": [{"name": "Has Row", "status": "ready", "bio_len": 120, "source": "lastfm"},
                 {"name": "No Row", "status": "no-row", "bio_len": 0, "source": None}]})
    bio.cmd_worklist(_args())
    out = capsys.readouterr().out
    assert "Has Row" in out and "No Row" not in out
