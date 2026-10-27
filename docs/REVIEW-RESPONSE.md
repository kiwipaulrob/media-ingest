# Review response — `gemini-code` brief of 2026-10-28 (reviewer read a stale tree)

The brief's Part 1 describes code as broken that HEAD had already fixed
(review email 10:24 NZDT; `45051b2` pushed 09:39 NZDT — the tree was
current, the clone was not). Item-by-item verdicts so the re-review can
skip straight to what is actually new.

## Part 1 — bugs (8)

| # | Verdict | Where fixed |
|---|---------|-------------|
| 1 `config.to_disk` | Already fixed | `cli/ingest_new.py:159-169` uses `place.to_disk` + `r["av_sync"]`; `res.setdefault("dest", dest)` present since the Oct-26 batch |
| 2 completeness/probe shape | Already fixed | `media_lib/completeness.py:97-107` explicit DIR/FILE/MISSING; `probe.py` returns `dur` + `size`; `tests/test_completeness.py` exists |
| 3 disk-vs-container paths | Already fixed | `media_lib/place.py:60` `to_jf()` with boundary-slash guard; applied in `awaiting.py:41,81` |
| 4 GET on Refresh | Already fixed | `cli/apply.py` `_refresh()` uses `refresh_item()` + `refresh_library()` (POST); docstring records the old GET failure |
| 5 tdarr import-time SystemExit | Already fixed | `scripts/tdarr_kick.py` lazy `_key()` at call time; comment records the CI collection abort |
| 6 avsync None start_time | Already fixed | `media_lib/avsync.py` filters `None` starts; comment records the false 300 ms disagreement |
| 7 usenet KeyError + s↔z | Already fixed | `usenet_triage.py` `.get(verdict, "skip")`; `tmdb.py` suffix-scoped regex with the `Ztoriez` comment |
| 8 locks + playback guard | **Real, fixed in this pass** | `safety.acquire()` added to `cli/bio.py` mutating commands; `is_playing()` guard before `swap.replace()` in `cli/apply.py` — see code comments marked `Review-8` |

## Part 2 — architecture (all real, this pass)

- `--from-plan` loop: `cli/ingest_new.py` accepts approved NEW rows without hand-written JSON (`Review-P2.1`).
- 4-field pinning at ingest: `tmdb.py` external_ids/credits/images fetch populates Imdb + Primary image + People (`Review-P2.2`).
- `.nfo` sidecars with `<lockdata>true</lockdata>` written alongside repaired files (`media_lib/nfo.py`, `Review-P2.3`).

## Part 3 — new sources (all real, this pass)

- TVmaze episode fallback for UK strands (`media_lib/providers/tvmaze.py`, `Review-P3.1`).
- MusicBrainz `url-rels` (`Review-P3.4`).
- Container-tag mining (`description`/`synopsis`/`comment`) in `probe.py` (`Review-P3.5a`).
- Transcript title mining stays a documented manual step (`references/transcript-title-mining.md`); faster-whisper automation deferred — needs CT110 GPU time, not code.
