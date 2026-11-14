"""plan.gate_entry: a localized torrent display name must not hide a film whose
payload filename gates clean (pure: tmdb.gate monkeypatched, no network)."""
import sys

sys.path.insert(0, ".")

from cli import plan  # noqa: E402


def _fake_gate(monkeypatch, mapping):
    def fake(name):
        status = mapping.get(name, "no-match")
        g = {"status": status, "query": name}
        if status == "candidate":
            g["candidate"] = {"id": 1, "title": "CBGB", "year": "2013"}
        return g

    monkeypatch.setattr(plan.tmdb, "gate", fake)


def test_display_name_candidate_needs_no_fallback(monkeypatch):
    _fake_gate(monkeypatch, {"Some.Film.2020.1080p": "candidate"})
    g = plan.gate_entry("Some.Film.2020.1080p", ["/dl/Some.Film.2020.1080p.mkv"])
    assert g["status"] == "candidate" and "via" not in g


def test_localized_display_name_falls_back_to_media_filename(monkeypatch):
    _fake_gate(monkeypatch, {
        "CBGB.2013.Blu.Ray.1080p.CINEMANIA.mkv": "candidate",
    })
    g = plan.gate_entry(
        "Клуб «CBGB» / CBGB (2013) BDRip 1080p от CINEMANIA   L2",
        ["/dl/CBGB.2013.Blu.Ray.1080p.CINEMANIA.mkv"],
    )
    assert g["status"] == "candidate", g
    assert g.get("via") == "media-filename-fallback"


def test_multi_file_torrent_gets_no_fallback(monkeypatch):
    _fake_gate(monkeypatch, {"e02.mkv": "candidate"})
    g = plan.gate_entry("Some Pack", ["/dl/e01.mkv", "/dl/e02.mkv"])
    assert g["status"] == "no-match", g


def test_identical_filename_gets_no_second_gate(monkeypatch, capsys):
    calls = []

    def fake(name):
        calls.append(name)
        return {"status": "no-match", "query": name}

    monkeypatch.setattr(plan.tmdb, "gate", fake)
    g = plan.gate_entry("Some.Film.2020.1080p", ["/dl/Some.Film.2020.1080p.mkv"])
    assert g["status"] == "no-match"
    assert calls == ["Some.Film.2020.1080p"], calls
