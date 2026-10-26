"""BBC fallback matching — parsing tests use fixtures, no network."""

import sys

sys.path.insert(0, ".")

from media_lib import bbc  # noqa: E402

_DDG = """
<html><body>
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.bbc.co.uk%2Fprogrammes%2Fp007v9p3">BBC Two - Stephen Hawking&#x27;s Universe</a>
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.bbc.co.uk%2Fprogrammes%2Fp007v9p3%2Fclips">Clips</a>
<a rel="nofollow" class="result__a" href="https://example.com/other">Not BBC</a>
</body></html>
"""

_GUIDE = """
<html><body>
<h2><a href="/programmes/p007v9zl">An Answer to Everything 6/6 synopsis here</a></h2>
<h2><a href="/programmes/p007v9yt">On the Dark Side 5/6 dark matter</a></h2>
<h2><a href="/programmes/p007v9p3">All episodes</a></h2>
</body></html>
"""

_PROG = """
<html><head><title>BBC Two - Stephen Hawking's Universe</title>
<meta name="description" content="Professor Stephen Hawking looks at the history." />
<script type="application/ld+json">{"@type":"TVSeries","name":"Stephen Hawking's Universe",
"description":"Full JSON-LD synopsis of the series."}</script>
</head></html>
"""


def _fake_fetch(url):
    if "duckduckgo" in url:
        return _DDG
    if "episodes/guide" in url:
        return _GUIDE
    return _PROG


def test_search_extracts_pid_once(monkeypatch):
    monkeypatch.setattr(bbc, "_fetch", _fake_fetch)
    hits = bbc.search_programmes("Stephen Hawking Universe")
    assert hits == [
        {"pid": "p007v9p3", "title": "BBC Two - Stephen Hawking's Universe"}
    ]


def test_search_never_raises(monkeypatch):
    def boom(url):
        raise bbc.BbcError("down")

    monkeypatch.setattr(bbc, "_fetch", boom)
    assert bbc.search_programmes("anything") == []


def test_episode_guide_parses_parts(monkeypatch):
    monkeypatch.setattr(bbc, "_fetch", _fake_fetch)
    eps = bbc.episode_guide("p007v9p3")
    assert [(e["pid"], e["part"], e["of"]) for e in eps] == [
        ("p007v9zl", 6, 6),
        ("p007v9yt", 5, 6),
    ]
    assert eps[0]["title"] == "An Answer to Everything synopsis here"


def test_programme_prefers_jsonld(monkeypatch):
    monkeypatch.setattr(bbc, "_fetch", _fake_fetch)
    p = bbc.programme("p007v9p3")
    assert p["title"] == "Stephen Hawking's Universe"
    assert p["synopsis"] == "Full JSON-LD synopsis of the series."


def test_match_empty_title():
    assert bbc.match("")["status"] == "no-match"
