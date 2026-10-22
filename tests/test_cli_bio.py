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


# ---------------------------------------------------------------- regressions
class _FakeResp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_wiki_batch_keys_a_redirect_under_the_requested_title(monkeypatch):
    """An alias target is usually a redirect; a bare title lookup called it a gap.

    'The Dakotas' -> 'Dakotas' is a real pair in state/alias-approved.json, so this
    was a live false 'GAP-NO-PROSE' on exactly the names the command exists for.
    """
    payload = {"query": {
        "redirects": [{"from": "The Dakotas", "to": "Dakotas"}],
        "pages": {"10": {"title": "Dakotas", "extract": "Dakotas were a British band. " * 4}},
    }}
    monkeypatch.setattr(bio.urllib.request, "urlopen", lambda *a, **kw: _FakeResp(payload))
    out = bio._wiki_batch(["The Dakotas"])
    assert "Dakotas" in out
    assert out.get("The Dakotas") == out["Dakotas"], "the requested title must resolve too"


def test_wiki_batch_resolves_a_normalized_title(monkeypatch):
    payload = {"query": {
        "normalized": [{"from": "bad title", "to": "Bad Title"}],
        "pages": {"1": {"title": "Bad Title", "extract": "Somebody was a director. " * 4}},
    }}
    monkeypatch.setattr(bio.urllib.request, "urlopen", lambda *a, **kw: _FakeResp(payload))
    assert "bad title" in bio._wiki_batch(["bad title"])


def test_alias_refuses_to_write_a_disambiguation_page(tmp_path, monkeypatch, capsys):
    """The alias path used to write whatever Wikipedia returned, unscreened."""
    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps([{"name": "Bernard MacMahon",
                                  "wiki_title": "Bernard MacMahon", "band_page": False}]))
    monkeypatch.setattr(bio, "_wiki_batch", lambda titles: {
        "Bernard MacMahon": "Bernard MacMahon or McMahon may refer to: a bishop, "
                            "a priest, and a horticulturist, among others."})
    monkeypatch.setattr(bio.enrich, "person_rows", lambda uid=None: {"Bernard MacMahon": {"Id": "p1"}})
    wrote = []
    monkeypatch.setattr(bio.enrich, "apply_person", lambda *a: wrote.append(a) or {"status": "DONE"})
    bio.cmd_alias(_args(file=str(pairs)))
    assert wrote == [], "a disambiguation page must never be written as a biography"
    assert "REJECT" in capsys.readouterr().out


def test_wikidata_skips_an_unverifiable_entity(monkeypatch, capsys):
    """No label means cannot verify, and an unverified one-liner is a wrong person."""
    monkeypatch.setattr(bio, "_wikidata_line", lambda n: "")
    monkeypatch.setattr(bio.enrich, "person_rows", lambda uid=None: {"X": {"Id": "p1"}})
    wrote = []
    monkeypatch.setattr(bio.enrich, "apply_person", lambda *a: wrote.append(a) or {"status": "DONE"})
    monkeypatch.setattr(bio, "_wikidata_line", lambda n: "")
    bio.cmd_wikidata(_args(names="X"))
    assert wrote == []


def test_wikidata_name_gate():
    assert bio._name_matches("Peter Grant", "Peter Grant")
    assert bio._name_matches("Peter Grant", "Peter Grant (musician)")
    assert bio._name_matches("peter  grant", "Peter Grant")
    assert not bio._name_matches("Peter Grant", "Grant Peters")
    assert not bio._name_matches("Peter Grant", ""), "an unlabelled hit must not pass"
