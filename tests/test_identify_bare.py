"""bare_movies + pick_identification: the Moog gap. Pure where possible."""

from media_lib import enrich


def test_pick_matches_gated_tmdb_not_position():
    cands = [{"Name": "Moog", "ProviderIds": {"Tmdb": "99999"}},
             {"Name": "Moog", "ProviderIds": {"Tmdb": "14978"}}]
    pick = enrich.pick_identification("Moog", 2004, cands, 14978)
    assert pick is cands[1]


def test_pick_refuses_when_gated_id_absent():
    cands = [{"Name": "Moog", "ProviderIds": {"Tmdb": "99999"}}]
    assert enrich.pick_identification("Moog", 2004, cands, 14978) is None
    assert enrich.pick_identification("Moog", 2004, [], 14978) is None
    assert enrich.pick_identification("Moog", 2004, cands, None) is None


def test_bare_movies_filters(monkeypatch):
    import media_lib.enrich as E
    rows = [
        {"Id": "a", "Name": "Bare", "People": [], "ProviderIds": {},
         "ProductionYear": 2004, "Path": "/x"},
        {"Id": "b", "Name": "Credited", "People": [{"Name": "X"}],
         "ProviderIds": {}, "ProductionYear": 2000, "Path": "/y"},
        {"Id": "c", "Name": "Identified", "People": [],
         "ProviderIds": {"Tmdb": "1"}, "ProductionYear": 2001, "Path": "/z"},
    ]
    monkeypatch.setattr(E.jellyfin, "user_id", lambda: "u")
    monkeypatch.setattr(E.jellyfin, "list_items", lambda *a, **k: rows)
    bare = E.bare_movies("parent")
    assert [b["id"] for b in bare] == ["a"]
    assert bare[0]["year"] == 2004


def test_thin_overviews_flags_filename(monkeypatch):
    import media_lib.enrich as E
    rows = [
        {"Id": "a", "Name": "Film", "Overview": "One.More.Time.2016.720p.BluRay-RARBG",
         "ProviderIds": {"Tmdb": "404141"}},
        {"Id": "b", "Name": "Full", "Overview": "A real description here.",
         "ProviderIds": {"Tmdb": "1"}},
        {"Id": "c", "Name": "NoId", "Overview": "", "ProviderIds": {}},
    ]
    monkeypatch.setattr(E.jellyfin, "user_id", lambda: "u")
    monkeypatch.setattr(E.jellyfin, "list_items", lambda *a, **k: rows)
    gaps = E.thin_overviews("parent")
    assert [(g["id"], g["reason"]) for g in gaps] == [("a", "filename-as-overview")]
