# Ingest code — what exists and what each part does

Location: `/root/.hermes/profiles/media-maintainer/media-ops/` (git repo, 12 commits)
Plugin: `/root/.hermes/profiles/media-maintainer/plugins/media-ingest/`
Totals: **5,201 lines** Python — 3,090 library + CLI, 1,127 tests, 495 scripts.

## The pipeline

```
media_source_plan  → source-plan.json  (read-only: what exists to download)
media_source_queue → qB / SAB          (queues explicit ids only)
media_scan   →  scan.json    (read-only: what finished downloading)
media_plan   →  plan.json    (read-only: REPLACE candidates + check list)
media_apply  →  swap        (DESTRUCTIVE: replaces a file that already has a row)
ingest_new   →  place       (MUTATING: adds a file that has NO row yet)
media_verify →  gate report (read-only: Tmdb/Imdb/image/overview per item)
```

`apply` and `ingest_new` are deliberately separate: apply retires an old file,
so it can only ever replace; a new episode has nothing to retire.

## Library modules (`media_lib/`)

| Module | Lines | Role |
|---|---|---|
| `config.py` | 106 | Path maps (`JF_TO_DISK`, `SAVE_TO_REMOTE`), media CT/host, image tag, cost constants |
| `jellyfin.py` | 248 | REST client: retrying GET (empty body = busy), full-DTO POST with minimal-body 400 fallback, `notify_media`, `refresh_library` |
| `qb.py` | 116 | qBittorrent Web API: completed candidates, video-file listing, container sniffing |
| `probe.py` | 127 | ffprobe/ffmpeg through a throwaway jellyfin container; magic-byte/poison detection |
| `matcher.py` | 216 | Title normalisation (`norm_tokens`, `squash`), stopword-filtered scoring |
| `library.py` | 84 | Library dump from the REST API, timestamped cache, row→disk mapping |
| `safety.py` | 141 | Ingest ledger (what was already processed), duplicate/hold guards |
| `swap.py` | 171 | Two-phase in-place replacement: copy → gates → retire old |
| `place.py` | 198 | Two-phase NEW placement: copy → gates (size/probe/height/duration/decode) |
| `tmdb.py` | 102 | TMDB lookups for the plan stage |
| `enrich.py` | 166 | Bio planning/applying, never-clobber, per-item journal |
| `repair.py` | 71 | Guarded bio repair (signature-guarded replace or clear) |

## Metadata providers (`media_lib/providers/`)

| Module | Lines | Role |
|---|---|---|
| `base.py` | 172 | `Record`/`Candidate`/`Provider` interface, merge with provenance, `BIOGRAPHY_BLOCKED` |
| `chain.py` | 252 | Resolution chain: identity gate, birth-year plausibility, junk-bio rejection |
| `cache.py` | 83 | On-disk provider cache (~2,600 entries), warm re-runs |
| `tmdb_p.py` / `lastfm.py` / `genius.py` / `deezer.py` | 75/88/83/57 | Prose + portrait providers |
| `theaudiodb.py` | 86 | Portraits/banners only — prose permanently blocked |
| `wikipedia.py` | 88 | Fallback prose + portraits |
| `musicbrainz.py` | 96 | Identity/alias only (rate-limited ~1 req/s) |

## CLIs (`cli/`)

| Command | Lines | Purpose |
|---|---|---|
| `python3 -m cli.scan` | 76 | Probes completed downloads not in the ledger |
| `python3 -m cli.plan` | 163 | Builds the decision table (REPLACE vs check list) |
| `python3 -m cli.apply --ids N` | 113 | Executes listed REPLACE ids only — no "all" switch |
| `python3 -m cli.ingest_new --spec F` | 117 | Places new files; `--wait-rows SECS` polls for the row |
| `python3 -m cli.verify` | 70 | 4-field exit gate |
| `python3 -m cli.meta plan\|apply` | 80 | Bio enrichment over person rows |

## Plugin verbs (`plugins/media-ingest/`)

`media_scan` · `media_plan` · `media_apply` · `media_verify`
— thin wrappers that shell out to the CLIs, so the tested library is the only
implementation. `media_apply` requires explicit ids by design.

## Safety invariants the tests enforce

1. **Never a full rescan** — `refresh_library` sends only `Recursive=true` with
   both ReplaceAll flags false (a full rescan purges deliberately-kept ghosts).
2. **A failing gate removes the staged copy** — no half-good file reaches the library.
3. **Size, duration, decode gates** — duration is compared to the *source's own*
   stream, so it runs even when the caller passes no expectation.
4. **2 remote calls per placed file** — pinned by a test; the first version used 6.
5. **Bio writes never clobber** — `repair` refuses unless the current text still
   contains the year it flagged.
6. **Pre-1900 births and junk bios are rejected** — a same-name collision guard.
7. **TheAudioDB cannot supply prose** — structurally blocked after it returned a
   2,000-character bio about the wrong band.

## State artefacts (`state/`)

| File | Meaning |
|---|---|
| `scan.json` / `plan.json` | Stage outputs |
| `ingest-new.jsonl` | Journal of every placement (idempotent re-runs) |
| `jellyfin-ingest-ledger.json` | Torrents already processed |
| `bio-repairs.jsonl` | Old and new text of every bio replaced or cleared |
| `meta-plan.json` / `meta-apply.jsonl` | Bio plan and applied writes |
| `meta-cache/` | Provider responses (~2,600 entries) |

## Sourcing (stage 0) — `media_lib/source.py`

Replaced four standalone hunt scripts whose duplication caused three real
defects in one session. Its rules exist because each one was learned the hard way:

| Rule | What went wrong |
|---|---|
| Fetch a broad pool, match in code | Ten per-title queries returned ZERO while one broad query returned 458 postings and matched 9 of 10 |
| Normalise before matching | `Black.Sabbath` never matched `"black sabbath"`; 3 of 4 Usenet grabs failed |
| Filter protocol explicitly | A "usenet" request returned torrents and a torrent was reported as a find |
| Advertised seeds are a floor, not evidence | 63 and 95 "seeds" both delivered 0 bytes and 0 peers |
| Flag `1ofN` postings | Rush and Tom Petty arrived as part 1 of 2 |
| `hash_of` must handle a group-less regex | `group(1)` raised IndexError and killed a hunt |

Matching runs the broad pool first, then ONE per-target query for anything the
pool missed (the two miss different postings: Queen, Deep Purple and Iron
Maiden are absent from every pool query but appear in a per-title search).

`media_source_queue` takes explicit ids only — no "all" switch — and refuses
incomplete sets unless `allow_incomplete` is set.
