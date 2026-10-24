"""TMDB title search: the British/American fallback must not mangle the title."""
from media_lib import tmdb


def _capture(monkeypatch):
    seen = []

    def fake_get(path, params):
        seen.append(params["query"])
        return {"results": []}

    monkeypatch.setattr(tmdb, "_get", fake_get)
    return seen


def test_s_z_swap_does_not_rewrite_every_s(monkeypatch):
    """A blanket s->z substitution turned "The Stories of Us" into "The Ztoriez of Uz".

    That variant was worse than no retry: it burned a request and could match the wrong
    title, so the swap is limited to real British/American suffixes.
    """
    seen = _capture(monkeypatch)
    tmdb.search("The Stories of Us")
    assert seen[0] == "The Stories of Us"
    assert not any("Ztoriez" in q or "of Uz" in q for q in seen), seen


def test_british_suffix_still_gets_an_american_retry(monkeypatch):
    seen = _capture(monkeypatch)
    tmdb.search("Organisation")
    assert "Organization" in seen, seen


def test_american_suffix_still_gets_a_british_retry(monkeypatch):
    seen = _capture(monkeypatch)
    tmdb.search("Organizer")
    assert "Organiser" in seen, seen
