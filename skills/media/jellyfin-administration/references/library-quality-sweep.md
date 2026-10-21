# Library Quality Sweep (resolution + codec)

Answer "what in this library is too low-resolution to keep, and what needs recoding?"
in ONE API pass — no `ffprobe`, no filesystem walk.

Jellyfin already indexes every stream's width, height and codec at scan time, so the
sweep is a read, not a probe. One pass covers 1,000+ items in seconds; probing the same
files over CIFS takes orders of magnitude longer and adds nothing.

Runnable form: `scripts/library-quality-sweep.py`.

## The API gotcha that silently wastes a run

**Use `Fields=MediaSources`. `Fields=MediaStreams` returns nothing** — no error, no
streams, every item reads back as `unknown` and the sweep looks like it "found no
problems" when it in fact read nothing.

Streams live at `MediaSources[0].MediaStreams[]`, so the request shape is:

```
GET /Items?ParentId=<libId>&Recursive=true&IncludeItemTypes=Movie,Video,Episode
    &Fields=Path,MediaSources,ProductionYear&Limit=500&StartIndex=N
```

Per item: first stream with `Type=='Video'` gives `Width`/`Height`/`Codec`; first with
`Type=='Audio'` gives the audio codec; `MediaSources[0].Container` gives the container.

**Sanity-check the sweep before trusting it:** if the summary says every item is
`unknown` (or zero items fall below the threshold), the query shape is wrong — not the
library. Verify against one known item (most libraries have an SD rip) before writing a
report.

## Enumeration

Page with `ParentId=<libId>&Recursive=true&Limit=500&StartIndex=N` until a page returns
fewer than 500 rows. Do **not** rely on `searchTerm` for auditing — it surfaces stale and
ghost rows that the paged listing no longer contains (same trap as the part-stacking
audit). Resolve library ids from `/Library/VirtualFolders` rather than hardcoding.

## Resolution banding

| Band | Meaning |
|---|---|
| `<=288p` | very low — clear redownload candidates |
| `289-480p` | SD; usually the realistic ceiling for old TV/PDTV broadcasts |
| `481-576p` | PAL SD |
| `577-719p` | odd non-standard sizes |
| `720p` / `1080p` / `>1080p` | fine |

"Below 720p" is the redownload flag. Expect it to be a large share of a long-standing
documentary library — that is normal, not a finding on its own.

## Recode-candidate detection

Flag by video codec OR container:

- **Video codecs:** `mpeg2video`, `mpeg4` (XviD/DivX), `msmpeg4v3`, `msmpeg4v2`,
  `msmpeg4`, `mpeg1video`, `wmv1`, `wmv2`, `wmv3`, `vc1`
- **Containers:** `avi`, `mpeg`, `mpg`, `vob`, `wmv`, `asf`, `flv`, `rm`, `rmvb`

The payoff is **direct-play compatibility, not quality** — XviD-in-AVI forces a transcode
on the N150 and plays badly. A recode can never add detail that was not captured, so never
sell one as a quality upgrade.

## Reporting — two outputs, always both

A sweep request asks for two distinct lists. Produce both, every time:

1. **Redownload candidates** (below-threshold resolution)
2. **Recode candidates** (legacy codec/container)

Write each to CSV plus a combined JSON, then summarise: band counts, codec/container mix,
and the worst offenders by name. Do **not** paste a 700-row wall into the reply — the CSVs
are the deliverable, the summary is the message.

**Do not present the raw below-threshold list as a to-do list.** Most of a long tail is old
broadcast material where no better version exists anywhere; tier by severity (the sub-288p
band is the genuinely actionable part) and say so. A 700-item "redownload these" list is
not an action plan.

## Triage: is each DOWNLOAD a replacement, new content, or already redundant?

The download side of the same question — "what did I fetch, and does it supersede
something I already hold?" — is a different pass from the library sweep above, and the
answer the user actually wants is a verdict per item, not a listing.

For every completed video torrent, probe **both sides** and compare **pixel dimensions**:

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height,codec_name -of csv=p=0 "<file>"
```

Then assign exactly one verdict:

| Verdict | Condition |
|---|---|
| **REPLACE** | the download is strictly higher resolution than the held copy (compare `width*height`, and quote the multiplier) |
| **NEW** | no counterpart exists in any library |
| **REDUNDANT** | the library already holds equal or better resolution — including the case where the download IS the same release already ingested |

Rules that keep this honest:

- **Resolution decides, never file size or age.** An old 1.57 GB file can be 704x400 while a
  0.4 GB file is 1904x1080; a size comparison called the SD file the bigger one. Report pixels.
- **Match by title stem across the whole library root, but treat a partial match as a lead,
  not a verdict** — a subfolder for one part of a multi-part work, or a trilogy folder holding
  the film as `...part 3`, will otherwise be read as "no counterpart" (NEW) when it is in fact
  a replacement of one part.
- **Check the REDUNDANT case first.** A download whose reported byte size equals an installed
  file's size is the same release already ingested — the tell is the size match, and it saves an
  entire swap cycle.
- **One download can cover more than the counterpart it matches** — a full-series rip supersedes
  every episode file, not just the one whose name it matched. Say so, or the swap silently
  upgrades one episode and leaves the rest.
- **Report the table, then ask.** The audit's output is the table; the swap is a separate,
  approved step. Never delete a superseded file as part of the audit pass.

## Pitfall: a sweep is a standing deliverable

A sweep spans many items and typically gets issued alongside other work. If an unrelated
blocker appears mid-turn, the sweep does not stop being owed — finish it, or carry it as an
explicitly-listed open item in the same reply. Silently dropping half a multi-part request
means the user has to come back later and ask whether it was ever done, and the honest
answer will be no.
