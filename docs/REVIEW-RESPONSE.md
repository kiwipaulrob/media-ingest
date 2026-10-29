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

## Second brief (`media_ingest_agent_review_and_metadata_plan.md`) — verdicts

Same staleness on Part 1 (bugs 1–7 identical citations, all fixed pre-brief).
Part 2–4 triaged item by item:
- Adopted: `git rm -r vendor/` (drift real, nothing imported it);
  `slot_check.py` + SLOT-COLLISION gate in `place_new` (gap real — placement
  budget now 3 remote calls, asserted in tests); series-Imdb fallback +
  SxxEyy IndexNumber backfill in `_fix_row_name`; TVmaze fallback already
  shipped, BBC `programme()` upgraded to pid.json (version-PID + empty-json
  lessons included); MusicBrainz url-rels already shipped; probe
  `synopsis` + `aired` container tags; `wikidata_claims.py` + read-only
  `bio portrait-search` (attaching stays approval-gated).
- Already true (brief wrong): sweep hardlink guard; plugin MEDIA_OPS_HOME;
  `cli/sync.py::tests()` already runs the plugin suite.
- Deferred with reason: transcript-mining CLI (needs CT110 GPU time, not
  code); ffmpeg frame-extract Primary fallback (still_path covers the common
  case; frame-pick needs a quality heuristic or it pins blurry frames).

## Review-3 (email 2133, vs 9f9e0a1 — current HEAD, no staleness)

| # | Claim | Verdict |
|---|-------|---------|
| P1.1a | pipe-joined hashes rejected by v5.x | ADOPTED: `_post_hashes()` one POST per hash; delete/setForceStart/reannounce routed through it |
| P1.1b | no nlink guard before delete_files | ADOPTED: `_hardlinked_hashes()` stats payload nlink via `place.remote_nlink()`; linked → entry-only delete (fail-open keeps files) |
| P1.2 | strict json.loads crashes | ADOPTED: sab `strict=False` + replace-decode |
| P1.3 | library cache omits Episode | ADOPTED: per-type Movie/Episode/Video queries (joined form distrusted per lesson 35); episode rows carry series/season/episode. Live: 2454 rows, 668 episodes |
| P1.4a | swap while playing | ALREADY DONE: `is_playing` skip in apply.py:114 |
| P1.4b | extension change wipes watch state | ADOPTED: pre-swap snapshot + post-refresh restore via set_played/set_playback_position when the row id changes |
| P1.5a | sync skips plugin tests | ALREADY DONE: sync.py:59 runs both suites |
| P1.5b | tools.py hardcoded paths | ADOPTED: MEDIA_OPS from env with fallback |
| P2.1 nfo sidecars | ALREADY DONE: media_lib/nfo.py |
| P2.2 4/4 exit gate | ALREADY DONE: _fix_row_name pins Imdb/Primary/People (ingest_new.py:466+) |
| P2.3 slot audit | ALREADY DONE: slot_check SLOT-COLLISION |
| P2.4 tvmaze/bbc/wikidata/url-rels | ALREADY DONE (+ resolve chain + imdb_search step 5) |
| P2.5 tidy consolidation | DECLINED: 57 scripts, each tested and reviewed; a merge adds risk for no gate benefit |
