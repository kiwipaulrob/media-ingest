# media-ingest

Tested, modular media ingest for a Jellyfin homelab — with a Hermes plugin, a
CLI stage pipeline, and a metadata provider chain.

This repository exists because ad-hoc ingest scripts written per task kept
failing in the same ways. Every rule in the code below is the scar of a specific
incident, and each one has a test that fails if it regresses.

**~4,400 lines of library code · 461 tests · no external dependencies beyond
the Python standard library**

---

## What problem it solves

Ingesting media means answering four questions safely:

1. **What finished downloading?** (`scan`)
2. **Does the library already have it, and is this better?** (`plan`)
3. **Replace the old file, or add a new one?** (`apply` vs `ingest_new`)
4. **Did the write actually land, and is it playable?** (`verify`)

The hard part is not any one step — it is that each step is destructive if it
guesses. A half-copied file, a mis-matched title, or a wrong synopsis written
over a good one is silent damage that surfaces days later.

So the design rule is: **stages are separate processes with artefacts between
them, read-only stages do all the thinking, and mutating stages only execute
explicit ids.**

---

## Architecture

```
                    ┌─────────────── read-only ───────────────┐
  Prowlarr ──────▶  source plan  ──▶ state/source-plan.json ──▶ (human picks ids)
  qBittorrent ───▶  scan         ──▶ state/scan.json
  library dump ──▶  plan         ──▶ state/plan.json ──▶ (human picks ids)
                    └────────────────────┬───────────────────┘
                                         │
                    ┌────────────────────▼───────────────────┐
                    │            mutating stages             │
  source queue  ──▶ qBittorrent / SABnzbd (explicit ids only)│
  apply         ──▶ swap: replace a file that HAS a row     │
  ingest_new    ──▶ place: add a file that has NO row yet   │
  verify        ──▶ 4-field gate report (read-only)         │
                    └────────────────────────────────────────┘
```

Nothing mutating ever scans a directory and decides. A mutating stage receives
either explicit plan ids or an explicit spec file, and refuses anything else.

### Why `apply` and `ingest_new` are separate

`apply` swaps one file for another and **retires the old file** — it can only
ever replace a row that already has a path. A genuinely new episode has nothing
to retire, so the first version of this code had no way to add one: three
episodes sat on disk, fully downloaded, with no stage that could place them.
`media_lib/place.py` is that missing stage, and it deliberately shares nothing
with `swap.py` except the gate philosophy.

---

## Module map

| Module | Purpose | Key idea |
|---|---|---|
| `config.py` | Paths, env, cost constants | One place for every mapping; two path namespaces (Jellyfin container paths vs media-host disk paths) |
| `jellyfin.py` | REST client | Retrying GET (an empty body means *busy*, not *no results*); full-DTO POST with a minimal-body fallback for 400s; `notify_media`; `refresh_library` |
| `qb.py` | qBittorrent client | Separator-tolerant name lookup; folder `content_path` resolution; ledger-aware candidate listing |
| `sab.py` | SABnzbd client | Queue/history reads; retryability classifier for failed slots |
| `probe.py` | ffprobe/ffmpeg over the media host | Runs a throwaway container with the same binaries the server uses; magic-byte and zero-payload detection |
| `matcher.py` | Release↔library matching | Stopword-filtered token scoring; squash-ratio; both signals must agree |
| `library.py` | Library dump | REST-derived, timestamped cache, container-path→disk-path mapping |
| `safety.py` | Ledger + lock | Single source of "already processed"; atomic writes |
| `swap.py` | Two-phase replacement | Copy → gates → only then retire the old file |
| `place.py` | Two-phase placement | Copy → 5 gates → notify; a failed gate removes the copy |
| `source.py` | Sourcing | Broad pool, normalised matching, ranking, queueing |
| `tmdb.py` | TMDB lookups | Episode resolution **by name**, never by number; `find_by_imdb()` for exact resolution from filenames carrying an imdb id |
| `enrich.py` | Bio planning/applying | Never-clobber; per-item journal |
| `jellyfin.py` | (also) `ensure_row_runtime()` — the only thing that clears a 0.0-min row after placement (matches Jellyfin `/data/…` and host `/mnt/…` path forms); `resolve_library()` — names to ItemIds; `delete_item` refuses container items |
| `repair.py` | Guarded bio repair | Signature-guarded replace or clear |
| `providers/` | Metadata chain | Interface + provenance merge; identity gate; birth-year plausibility; junk-bio rejection |

---

## Beyond ingest: dedupe, merge, concat, series-convert

Placing downloads is only half the job. The same safety philosophy (read-only
thinking, explicit targets, verify by reading back) covers four more verbs:

| Verb | Entry point | Tests | Rule |
|---|---|---|---|
| dedupe (find) | `scripts/find_dups.py` (read-only report) | `tests/test_find_dups.py` | provider-id-first identity; a group needs >1 row on >1 distinct path, so multi-version items never flag |
| dedupe (resolve) | hardlink (`cmp -s` then `ln -f`, require `links>=2` both paths) or `<primary> - <label>` alternate versions in one folder | verified by read-back (`links`, `sources=2`) | judge hardlinks by link count, never inode (CIFS gives synthetic inodes); different duration = different cut, keep separate |
| merge (cuts/encodes) | same alternate-version naming | — | `<primary> - <label>` merges; `- partN` does **not** (server-side, verified twice each way) |
| concat (2-part film) | `scripts/concat_parts.py [--reencode]` (dry-run default) | `tests/test_concat_parts.py` | part-number ordering (never the year); output duration within 2% of the parts' sum or the parts stay |
| series-convert (3+ parts) | `scripts/multipart_to_series.py` | guards, no dedicated tests yet | existing-series abort; provider-count match; per-episode runtime ratio 0.75–1.33; leftover-source hash check at the end |
| series-shape scan | `scripts/scan_series_shapes.py [--from-stdin]` (read-only) | `tests/test_scan_series_shapes.py` | bare `e01` matches, bare `e 8` never does; remote listings via stdin (media is not local) |
| hunt (plan) | `media_lib/hunt.py::plan` (read-only) | `tests/test_hunt.py` | token-set title matching (dotted = spaced); protocol/seeder/episode filters; seed+grab rank; queue takes one explicit candidate only |
| fleet-health | `media_lib/fleet.py::check` (read-only) | `tests/test_fleet_wanted.py` | never raises; red rows are human decisions, never auto-repairs |
| wanted sweep | `scripts/wanted.py` + `state/wanted.json` (read-only) | `tests/test_fleet_wanted.py` | watchlist hunts via hunt.plan; never queues |
| slot gate | `media_lib/slot_check.py` (1 remote `ls`, pre-copy) | `tests/test_slot_check.py` + place budget tests | disk is truth on SxxEyy slots; SLOT-COLLISION refuses before a byte copies |
| tvmaze fallback | `media_lib/tvmaze.py` (keyless, name-first) | `tests/test_tvmaze.py` | UK strands where TMDB shells/misses; never overrides TMDB |
| wikidata claims | `media_lib/wikidata_claims.py` + `bio portrait-search` | `tests/test_wikidata_claims.py` | P345/P569/P106/P18 bridge; URLs printed, attaching stays approved |
| bbc pid.json | `media_lib/bbc.py::programme` | `tests/test_bbc.py` | version PIDs resolve via parent; empty json falls back to HTML |
| nfo sidecars | `media_lib/nfo.py` (written on repair) | `tests/test_nfo.py` | lockdata survives Tdarr swaps and row recreation |
| series audit | `scripts/audit_series.py --library` (read-only) | `tests/test_audit_series.py` | flags no-tmdb/no-imdb/short-overview/no-img per series row |
| exhaustive resolve | `media_lib/resolve.py` (read-only chain) | `tests/test_resolve.py` | TMDB -> TVmaze -> BBC -> local-nfo with per-field provenance; dead sources never stop the chain |
| episode audit | `scripts/audit_episodes.py --library` (read-only) | `tests/test_audit_episodes.py` | path-aware: virtual TMDB slots are not gaps; counts-first, detail to JSON |
| episode pinning | `scripts/pin_episode_ids.py --library --all [--apply]` | `tests/test_pin_episode_ids.py` | TMDB episode ids matched BY NAME only; ties refused; never by number |
| aspect flags | `scripts/scan_aspect_flags.py [--libs ...]` | `tests/test_aspect_flags.py` | flags wide rasters with narrow DAR flags (Gilbert & George class); legit anamorphic never flags |
| dupe prune | `scripts/prune_lateral_dupes.py` (dry-run default) | `tests/test_prune_dupes.py` | deletes only triple-proven lateral dupes (library match, no upgrade, shelf probes healthy); explicit --ids, no bulk switch |

2-part films stay films (concat or nothing); 3+ parts default to series style.
`migrate_series_between_libs.py` moves series between libraries on exact TMDB
match only (no dedicated tests yet — labelled so in the pipeline skill).

---

## The invariants (and the incidents behind them)

These are the rules a reviewer should check any change against. Each is covered
by a test.

| Invariant | Incident |
|---|---|
| **Never run a full library rescan.** `refresh_library` sends only `Recursive=true`, both ReplaceAll flags false. | A full rescan purges ghost rows that are deliberately kept |
| **A refresh does not discover new files.** Placement must call `notify_media` (`POST /Library/Media/Updated`). | Two episodes were invisible for 10 minutes; the refresh returned zero rows |
| **One notify can be dropped — re-notify.** Notify, poll ~60 s, notify again, up to twice. | Rows appeared after the 1st, 2nd *and* 3rd notify on different runs |
| **A completed copy is not proof of playability.** Gates: size, probe, min-height, duration-vs-source, decode at 3 spans. | A torrent at 100% delivered a file of zeros |
| **A failing gate removes the staged copy.** | A half-good file must never stay in the library |
| **Duration is compared to the source's own stream**, not a caller hint. | The gate silently never ran when the caller omitted it |
| **Two remote calls per placement.** | The first version used six (~8.5 s of SSH + container startup per file) |
| **Never trust advertised seeders.** They are a floor for offering a pick, never a ranking authority. | "63 seeds" and "95 seeds" both delivered zero bytes and zero peers |
| **Normalise before comparing names.** Dots, hyphens and spaces are the same title. | `'Stevie Wonder' in name` missed `Classic-Albums-Stevie-Wonder-…` three times |
| **Resolve a folder `content_path` to its largest video.** | Joining `content_path` + the file's relative name produced a doubled path → `SRC-MISSING` |
| **Record the ledger at placement time.** | Two ledgers existed, so `scan` kept re-offering already-ingested releases |
| **Resolve TMDB episodes by name, never by number.** | Season 4 episode 1 returned the Def Leppard *Hysteria* episode for a Sex Pistols slot; season 9 episode 2 returned Tom Petty for John Lennon — both wrote a wrong synopsis and a wrong still |
| **A pre-1900 birth year is a name-collision signal.** | A living crew member received a 19th-century naval officer's biography |
| **Some providers may never supply prose.** `BIOGRAPHY_BLOCKED` (TheAudioDB). | It returned a 2,000-character bio about a French power-metal band for the Sarah Records band *Heavenly* |
| **TheAudioDB's demotion is structural**, not a priority hint. | The "unknown providers rank last but still count" fallback quietly let it back in |
| **Repair never clobbers**: refuse unless the current text still contains the flagged year. | Two approved aliases (*Francis Hanly*, *James Hale*) had written wrong-person bios |
| **A 204 is not evidence.** Verify by reading the row back (providers, overview, images, runtime) — three separate 204s meant nothing until read back. | Identify calls that "succeeded" while rows stayed at 0.0 min with no metadata |
| **A guard constant must be read, never estimated.** Comparing against a byte count derived from a megabyte display refuses valid work or passes by coincidence. | A hardcoded size from a rounded MB figure aborted a valid delete; operator precedence (`&&`/`||`) silently neutered another |
| **A search returning zero is a failed query until a control query returns hits.** | A full-title query returned literally zero while the short form returned 29 — reported as "not available" before the control ran |

---

## Do not duplicate the clients

There were four standalone hunt scripts that each reimplemented Prowlarr search,
title matching and the qBittorrent client. That duplication caused three of the
defects above — and a fourth (`hash_of()` calling `group(1)` on a group-less
regex) crashed a hunt outright.

They have been replaced by `media_lib/source.py` and deleted.

**If you are an agent reading this: use `source.py`, `qb.py`, `sab.py`,
`jellyfin.py`. Do not write a new script that calls these APIs directly.**

---

## Quick start

```bash
# Environment (never commit these)
export JELLYFIN_URL=http://jellyfin:8096
export JELLYFIN_API_KEY=...        # 32-char hex
export JELLYFIN_USER_ID=...        # admin user id, for user-scoped reads
export TMDB_API_KEY=...
export QB_URL=http://qbittorrent:8090
export PROWLARR_URL=http://prowlarr:9696
export PROWLARR_API_KEY=...
export SABNZBD_URL=http://sabnzbd:8095
export SABNZBD_API_KEY=...
export MEDIA_IMAGE=jellyfin/jellyfin:12.2   # image used for ffprobe/ffmpeg

# Sourcing (read-only, then queue explicit ids)
python3 -m cli.source plan  --targets targets.json
python3 -m cli.source queue --ids 1,4

# Ingest
python3 -m cli.scan
python3 -m cli.plan
python3 -m cli.apply --ids 1                # replace
python3 -m cli.ingest_new --spec spec.json  # place new
python3 -m cli.verify --json

# Tests
python3 -m pytest tests/ -q
```

### Placement spec

```json
[
  {
    "src":  "/downloads-zmedia/Some.Release.mkv",
    "dest": "/mnt/zmedia/videos/Series/Season 02/Series (1997) S02E03 Thing.mkv",
    "min_height": 700
  }
]
```

`dest` is explicit on purpose: the scanner keys rows off the **path**, so a
normalised-title guess creates a second row instead of filling the intended one.

---

## Hermes plugin

`vendor/plugins/media-ingest/` exposes the stages as agent tools:

| Tool | Mutating? | Contract |
|---|---|---|
| `media_scan` | no | lists completed candidates with real resolution/codec |
| `media_plan` | no | REPLACE ids + a "needs your check" summary |
| `media_apply` | **yes** | explicit ids only; no "all" switch by design |
| `media_verify` | no | 4-field gate per item |
| `media_source_plan` | no | numbered shortlist across torrent + usenet |
| `media_source_queue` | **yes** | explicit ids; refuses incomplete `1ofN` sets |

Install: copy to `~/.hermes/plugins/`, `hermes plugins enable media-ingest`,
restart the gateway. New tools appear in the *next* session — a running session's
tool catalog is built at start-up.

---

## Where an agent should look first

1. **`docs/INGEST.md`** — the stage contract and every state artefact.
2. **`media_lib/place.py`** — the newest stage; its docstring carries the cost
   model and the gate order.
3. **`tests/`** — read the tests before changing a stage. They encode *why*, not
   just *what*: `test_place.py` pins the two-remote-call budget;
   `test_source.py` pins dotted-name matching and the part-set rule;
   `test_tmdb_episode.py` pins name-over-number resolution.

## Known weak spots (honest list)

- `cli/plan.py` still routes genuinely NEW episodes to the "needs your check"
  list; a `NEW` decision row with a proposed destination would close the loop
  and remove the hand-written spec.
- Placements run sequentially. Copying is I/O-bound (~39 MB/s to CIFS) and the
  gates are network-bound, so 2–3 workers should roughly double batch
  throughput.
- `config.COPY_MBPS = 158` is a local-disk measurement and overstates this CIFS
  path ~4× — budget at ~40 MB/s.
- A failed placement re-copies from zero; `.part` files are removed rather than
  resumed.
- `cli/meta.py` took a library name where an ItemId was needed; `jellyfin.resolve_library()` now accepts either (with 3 tests).
- **Not packaged** (`pip install -e .`), so the plugin shells out to `cli.*` with a
  hardcoded `MEDIA_OPS` path. Decided 2026-10-22 and **kept under review** — the
  full reasoning and the five conditions that would reopen it are in
  `docs/REVIEWS.md` § Deferred. Short version: the symlink already removed the
  duplicated-copy problem packaging was meant to fix, and importing would lose the
  process boundary and the "changes apply on the next tool call" property.
- Provider cache has no eviction.

## Licence

Private homelab tooling, published for reference. No warranty.
