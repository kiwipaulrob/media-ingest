"""Row-name verification: catch the provider overwriting a freshly-placed row.

The real case these tests encode (2026-10-22): placing into a ghost row let the
post-placement refresh resolve the slot by episode NUMBER, and this library uses
scene numbering, so S03E05 came back as "Judas Priest: British Steel" while the
file on it was Elvis Presley, and S10E02 became "Peter Gabriel: So" over the Tom
Petty file.

The comparison must be loose enough to survive release-vs-library formatting and
strict enough to catch a genuinely different episode.
"""
from media_lib import rowcheck


# ---------------------------------------------------------------- real failures
def test_catches_the_real_elvis_mismatch():
    path = ("/mnt/zmedia/videos/musicdocos-series/Classic Albums (1997) [tmdbid-9048]"
            "/Season 03/Classic Albums (1997) S03E05 Elvis Presley - Elvis Presley.mkv")
    assert rowcheck.names_match(path, "Elvis Presley: Elvis Presley")
    assert not rowcheck.names_match(path, "Judas Priest: British Steel")


def test_catches_the_real_petty_mismatch():
    path = ("/mnt/zmedia/videos/musicdocos-series/Classic Albums (1997) [tmdbid-9048]"
            "/Season 10/Classic Albums (1997) S10E02 Tom Petty and The Heartbreakers"
            " - Damn the Torpedoes.mkv")
    assert rowcheck.names_match(path, "Tom Petty and the Heartbreakers: Damn the Torpedoes")
    assert not rowcheck.names_match(path, "Peter Gabriel: So")


# ------------------------------------------------------------ formatting drift
def test_survives_ampersand_and_punctuation_drift():
    path = "S01E06 Fleetwood Mac - Rumours.mkv"
    assert rowcheck.names_match(path, "Fleetwood Mac: Rumours")

    path = "S10E10 Soul II Soul Club Classics Vol 1.mkv"
    assert rowcheck.names_match(path, "Soul II Soul: Club Classics Vol. One")

    path = "S10E07 The Crickets The Chirping Crickets.mkv"
    assert rowcheck.names_match(path, "The Crickets: The Chirping Crickets")


def test_short_album_names_still_count():
    """'Aja' is exactly 3 characters and was a false positive at a >3 floor."""
    assert rowcheck.names_match("S02E05 Aja.mkv", "Steely Dan: Aja")


def test_the_artist_added_by_the_library_is_not_a_mismatch():
    assert rowcheck.names_match("S18E6 Frank Zappa - Freak Out.mkv",
                                "The Mothers of Invention: Freak Out!")


# ------------------------------------------------------------------- guard rails
def test_nothing_distinctive_to_compare_is_not_a_mismatch():
    """Never claim damage on an empty comparison — that would cry wolf."""
    assert rowcheck.names_match("S01E01 1080p x264.mkv", None)
    assert rowcheck.names_match("S01E01 the and with.mkv", "Anything")


def test_identity_extraction_handles_all_video_extensions():
    for ext in ("mkv", "mp4", "avi", "MKV"):
        assert rowcheck.identity_from_filename(f"S03E05 Elvis Presley - Elvis Presley.{ext}") \
            == "Elvis Presley - Elvis Presley"


def test_identity_falls_back_to_the_bare_name():
    assert rowcheck.identity_from_filename("no episode token here.mkv") \
        == "no episode token here.mkv"


def test_describe_mismatch_names_both_sides():
    text = rowcheck.describe_mismatch("S03E05 Elvis Presley - Elvis Presley.mkv",
                                      "Judas Priest: British Steel")
    assert "Judas Priest: British Steel" in text and "Elvis Presley" in text


def test_first_mismatch_scans_a_batch():
    rows = [("S01E01 U2 - The Joshua Tree.mkv", "U2: The Joshua Tree"),
            ("S03E05 Elvis Presley - Elvis Presley.mkv", "Judas Priest: British Steel")]
    found = rowcheck.first_mismatch(rows)
    assert found and "Elvis Presley" in found
    assert rowcheck.first_mismatch([("S01E01 U2 - The Joshua Tree.mkv",
                                     "U2: The Joshua Tree")]) is None


# ------------------------------------------------- the guard in the placement CLI
def test_check_row_names_flags_a_mismatch_and_writes_the_check_list(monkeypatch, tmp_path):
    """The wiring, not just the comparison: a mis-resolved row must be reported.

    Hermetic by construction — the real check list is monkeypatched to tmp_path.
    A test that writes to the live state dir is how CI failed once before.
    """
    from cli import ingest_new as cli

    row = {"id": "abc", "Path": "/lib/S03E05 Elvis Presley - Elvis Presley.mkv",
           "Name": "Judas Priest: British Steel"}
    monkeypatch.setattr(cli.jellyfin, "user_id", lambda: "uid")
    monkeypatch.setattr(cli.jellyfin, "get_json", lambda path, params=None: dict(row))
    monkeypatch.setattr(cli.config, "CHECK_LIST", tmp_path / "needs-your-check.json")

    journal = tmp_path / "ingest-new.jsonl"
    problems = cli.check_row_names([{"item_id": "abc", "dest": row["Path"]}],
                                   journal=journal)

    assert len(problems) == 1
    assert problems[0]["row_name"] == "Judas Priest: British Steel"
    assert problems[0]["expected"] == "Elvis Presley - Elvis Presley"
    assert "Elvis Presley" in problems[0]["detail"]

    written = __import__("json").loads((tmp_path / "needs-your-check.json").read_text())
    assert written[0]["id"] == "abc"
    events = journal.read_text().strip().splitlines()
    assert '"event": "row-name-mismatch"' in events[0]


def test_check_row_names_is_quiet_when_the_row_is_right(monkeypatch, tmp_path):
    from cli import ingest_new as cli

    row = {"id": "abc", "Path": "/lib/S03E05 Elvis Presley - Elvis Presley.mkv",
           "Name": "Elvis Presley: Elvis Presley"}
    monkeypatch.setattr(cli.jellyfin, "user_id", lambda: "uid")
    monkeypatch.setattr(cli.jellyfin, "get_json", lambda path, params=None: dict(row))
    monkeypatch.setattr(cli.config, "CHECK_LIST", tmp_path / "needs-your-check.json")

    journal = tmp_path / "ingest-new.jsonl"
    assert cli.check_row_names([{"item_id": "abc", "dest": row["Path"]}], journal=journal) == []
    assert not journal.exists()
    assert not (tmp_path / "needs-your-check.json").exists()


def test_check_row_names_skips_rows_that_have_not_appeared_yet(monkeypatch, tmp_path):
    """A row that is 'not yet' discovered is not a mismatch — do not cry wolf."""
    from cli import ingest_new as cli
    monkeypatch.setattr(cli.jellyfin, "get_json", lambda *a, **k: {})
    monkeypatch.setattr(cli.config, "CHECK_LIST", tmp_path / "check.json")
    assert cli.check_row_names([{"item_id": "", "dest": "/lib/x.mkv"}],
                               journal=tmp_path / "j.jsonl") == []
