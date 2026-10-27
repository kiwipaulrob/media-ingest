"""wikidata_claims pure parsing + verified-search gating (no network)."""
import sys

sys.path.insert(0, ".")

from media_lib import wikidata_claims as wc  # noqa: E402

ENTITY = {"claims": {
    "P345": [{"mainsnak": {"datavalue": {"value": "tt1234567", "type": "string"}}}],
    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1940-10-09T00:00:00Z"},
                                        "type": "time"}}}],
    "P106": [{"mainsnak": {"datavalue": {"value": {"id": "Q639669"}, "type": "wikibase-entityid"}}}],
    "P18": [{"mainsnak": {"datavalue": {"value": "John Lennon 1969.jpg", "type": "string"}}}],
}}


def test_parse_claims_extracts_values():
    out = wc.parse_claims(ENTITY)
    assert out["P345"] == ["tt1234567"]
    assert out["P569"] == ["+1940-10-09T00:00:00Z"]
    assert out["P106"] == ["Q639669"]
    assert out["P18"] == ["John Lennon 1969.jpg"]


def test_names_match_rejects_fuzzy():
    assert wc._names_match("John Lennon", "John Lennon") is True
    assert wc._names_match("John Lennon", "John Lennox") is False
    assert wc._names_match("John Lennon", "") is False


def test_imdb_and_birth_and_portrait(monkeypatch):
    monkeypatch.setattr(wc, "search_qid", lambda *a: "Q123")
    monkeypatch.setattr(wc, "claims", lambda *a: wc.parse_claims(ENTITY))
    assert wc.imdb_id("John Lennon") == "tt1234567"
    assert wc.birth_year("Q123") == "1940"
    url = wc.portrait_url("John Lennon")
    assert url.startswith("https://commons.wikimedia.org/wiki/Special:FilePath/")
    assert "John%20Lennon" in url


def test_unknown_returns_empty(monkeypatch):
    monkeypatch.setattr(wc, "search_qid", lambda *a: "")
    assert wc.imdb_id("Nobody Real") == ""
    assert wc.portrait_url("Nobody Real") == ""
