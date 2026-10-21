# GUH vs 3JY `videos/` audit (6 Sep 2026)

Question asked: were the videos on GUH and 3JY consolidated as part of the Jellyfin install? Answer: **no** — no merge ever happened. Jellyfin CT105 mounts both drives but its compose maps ONLY GUH folders into the libraries (`/mnt/media/videos/Films:/data/movies:ro`, etc.); the 3JY mount (`/mnt/db1`) sits unused.

## Method (cheap, no hashes)

Basename comparison — fast enough for 7k+ files where content-hashing would take hours:

```bash
find /mnt/dguh/videos -type f -printf "%f\n" | sort > /tmp/guh_vids.txt
find /mnt/db1/videos -type f -printf "%f\n" | sort > /tmp/j3y_vids.txt
comm -12 /tmp/guh_vids.txt /tmp/j3y_vids.txt | wc -l   # common
comm -13 /tmp/guh_vids.txt /tmp/j3y_vids.txt           # unique to 3JY
comm -23 /tmp/guh_vids.txt /tmp/j3y_vids.txt | wc -l   # unique to GUH
```

## Results

| | Count |
|---|---|
| GUH files | 5,168 |
| 3JY files | 2,721 |
| Common basenames | 2,706 |
| Unique to 3JY | 15 |
| Unique to GUH | 2,462 |

Per-dir file counts (GUH vs 3JY): Documentaries 913/761, Films 2169/1899, TV 698/2, filmnoir 15/15 (identical dir listings both sides), musicdocos 1373/44.

The 15 unique-to-3JY basenames: 13 film-noir titles (`Alphaville (1965)`, `Angel Heart (1987)`, `Gilda (1946)`, `In a Lonely Place`, `Mildred Pierce`, `Out of the Past`, `Ride the Pink Horse`, `Rififi (1955)`, `The Big Sleep (1946)`, `The Killers (1946)`, `The Killing (1956)`, `The Naked City`, `The Spiral Staircase`) + 2 FileBrowser-download temp files (`2025-03-16-05-39_265132508.mp4`, `2025-04-01-07-12_17265714200.mp4`). The 13 noir titles exist under GUH's `filmnoir/` with year-suffixed dir names — same films, possibly same bytes.

## Implication for the archive mirror plan

3JY's `videos/` (~784G) is ~99% name-duplicate of GUH. Once the 15 are verified playable from GUH, 3JY's tree can be DELETED rather than moved — frees the space without adding to FUJH.

## Verification (6 Sep 2026, post-audit)

Full-file md5 on all 15 uniques vs their GUH counterparts: **all 15 byte-identical** (13 noir titles + both FileBrowser temp files). The 2 "no match" temp files match GUH copies under proper release names, same sizes, same hashes:

| 3JY file (FileBrowser-download name) | GUH counterpart (release name) | md5 |
|---|---|---|
| `filmnoir/Dial M for Murder (1954)/2025-03-16-05-39_265132508.mp4` (1.7G) | `filmnoir/Dial M for Murder (1954)/Dial.M.for.Murder.1954.1080p.BluRay.x265-RARBG.mp4` | 180d3e13…64ad |
| `filmnoir/Pickup on South Street (1953)/2025-04-01-07-12_17265714200.mp4` (1.6G) | `filmnoir/Pickup on South Street (1953)/Pickup.on.South.Street.1953.CRITERION.1080p.BluRay.H264.AAC-RARBG.mp4` | b300551c…247a0 |

Conclusion: **nothing needs copying to GUH — every byte of 3JY's `videos/` already exists on GUH.** Option 1's copy step is vacuous; 3JY's tree (`/mnt/db1/videos`, 2,721 files, ~784G) is safe to DELETE once the user gives explicit go-ahead. md5 full-file match supersedes any spot-play check.

## Kiss Me Deadly replacement (user-approved DB exception, 06 Sep 2026 ~21:10 NZST)

New 4.0 GB BDRip (`Celuj.menya.nasmert.1955.BDRip.AVC.mkv`, h264 960×576 + 3×AC3 + rus/eng subs) landed in the Kiss Me Deadly ghost dir — but a rename-to-DB-name fix was IMPOSSIBLE: the DB-expected name `Kiss.Me.Deadly.1955.BRRip.XviD.MP3-XVID.avi` ends in `.avi` while the real bytes are Matroska (`1a 45 df a3`). Mkvs wearing `.avi` names always fail (Jellyfin forces `-f avi` from the extension → `Invalid data found`).

Sanctioned ONE-TIME exception to the never-rewrite-the-DB rule, with explicit user approval: staged single-row swap (`/tmp/jellyfin-fix.db`, integrity `ok`), stop container → timestamp backups (`/tmp/jellyfin.db.live-pre-swap-*` + `SQLiteBackups/jellyfin.db.pre-kissme-*`) → `UPDATE BaseItems SET Path='.../Kiss Me Deadly (1955).mkv', Size=4272898081, Data=json_replace(...,'$.Container','mkv',...)` → replaced 2 stale MediaStreams with 6 fresh ffprobe rows → delete -shm/-wal → restart. Backups kept for rollback.

Follow-on audio fix: mkv's default flag sat on Russian dub track (a1). Ran on proxmox3: `mkvpropedit ... --edit track:a1 --set flag-default=0 --edit track:a3 --set flag-default=1` → English mono now default (verified via mkvinfo + container ffprobe). No DB touch needed — Jellyfin honors the container default flag when user prefs are unset. If a user still hears Russian: set that user's AudioLanguagePreference='eng' (Users.AudioLanguagePreference, empty = follow container flags).

**Template for future container-lying replacements**: (1) `head -c 16 | od` magic check — mkv=`1a 45 df a3`, avi=`RIFF`; (2) extension MUST match bytes — rename disk to true extension; (3) single-row staged DB swap (same recipe); (4) `mkvpropedit` default-flag audit for multi-audio mkvs; (5) restart + hard-refresh client (Ctrl+Shift+R) before re-diagnosing — stale player sessions show old errors with no new server logs.

`rm -rf /mnt/db1/videos` required a `mount -o remount,rw /mnt/db1` first (fstab pins db1 `ro` — unlike dguh which is `rw`). Tree gone: 3JY freed 3.6T→2.9T used (8.1T avail). GUH untouched (3.4T used / 7.6T avail, 5,168 files intact).

## Film-noir ghost inventory (live GUH vs DB, 06 Sep 2026 ~19:30 NZST)

DB expects 31 feature-film paths under `/data/filmnoir/`; only **15 folders on GUH contain files** — the other **16 are empty dirs** (ghosts: entries play nothing until a replacement file is dropped in). Empty = needs a replacement:

| # | Film (GUH dir) | DB-expected filename | Status |
|---|---|---|---|
| 1 | Double Indemnity (1944) | Double.Indemnity.1944.1080p.BluRay.10Bit.HEVC.EAC3-SARTRE.mkv | ❌ empty — needs file |
| 2 | Kiss Me Deadly (1955) | Kiss.Me.Deadly.1955.BRRip.XviD.MP3-XVID.avi | ❌ empty |
| 3 | Laura (1944) | laura.1944.1080p.bluray.x264-hd4u.mkv | ❌ empty |
| 4 | Los Angeles City of Film Noir (2015) | Los.Angeles.Film.Noir.720p.WEB-DL.x264.AAC.MVGroup.Forum.mkv | ❌ empty |
| 5 | Man Hunt (1941) | Man.Hunt.1941.1080p.BluRay.x264.YIFY.mp4 | ❌ empty |
| 6 | Ministry of Fear (1944) | Ministry.of.Fear.1944.(Noir-Fritz.Lang).1080p.BRRip.x264-Classics.mkv | ❌ empty |
| 7 | Murder by Contract (1958) | Murder.by.Contract.1958.Indicator.1080p.BluRay.x265.HEVC.FLAC-SARTRE.mkv | ❌ empty |
| 8 | Notorious (1946) | Notorious (1946) Criterion (1080p BluRay x265 afm72).mkv | ❌ empty |
| 9 | Sweet Smell of Success (1957) | Sweet Smell of Success 1957 1080p Criterion Bluray x265 LION.mkv | ❌ empty |
| 10 | The Big Heat (1953) | The Big Heat (1953).mkv | ❌ empty |
| 11 | The Maltese Falcon (1941) | The.Maltese.Falcon.1941.1080p.BluRay.HEVC.AAC-SARTRE.mkv | ❌ empty |
| 12 | The Night Of The Hunter (1955) | The.Night.Of.The.Hunter.1955.720p.BRRip.x264-x0r.mkv | ❌ empty |
| 13 | The Rules Of Film Noir (2009) | BBC.The.Rules.of.Film.Noir.1080p.HDTV.x265.AAC.MVGroup.org.mkv | ❌ empty (note: dir title-case `Of`, DB path lowercase `of` — drop file in the existing dir) |
| 14 | The Third Man (1949) | The.Third.Man.1949.Criterion.1080p.BluRay.x264-OFT.mkv | ❌ empty |
| 15 | Touch of Evil (1958) | Touch.of.Evil.1958.RM4K.Restored.1080p.BluRay.x265.HEVC.FLAC-SARTRE.mkv | ❌ empty |
| 16 | The Killers Extra | The Killers Extra.avi (same dir as The Killers.avi) | ❌ empty (companion extra, same folder — optional) |
| 17 | Kiss Me Deadly (1955) | Kiss Me Deadly (1955).mkv | ✅ FILLED 6 Sep 2026 — Cause B+C exception swap, see section above |

**Replacement intake checklist** (run BEFORE the file lands, saves a DB swap): (1) `head -c 16 newfile | od -A x -t x1z` — extension must match bytes (mkv=`1a 45 df a3`, avi=`RIFF`); if the ghost-row name lies about the container, plan the staged single-row DB swap up front. (2) Multi-audio mkv? `mkvinfo` default-flag audit — English must hold the flag. (3) `mkvtoolnix` lives on proxmox3 (`apt install mkvtoolnix`), edits run there natively. (4) After landing: hard-refresh client before testing (stale sessions fake failures).

Replacement rule: name the file EXACTLY as the DB-expected filename (column 3 — Jellyfin resolves by exact path, no rescan needed per user rule: never rescan, ghosts stay until lit). Drop it in the existing empty dir; entry lights up on next browse. Easiest route is FileBrowser upload to `db1`… no — GUH: `files.robertsons.cloud` → dguh/videos/filmnoir/&lt;dir&gt; — but GUH's Samba share is read-only, so FileBrowser upload will FAIL; use SMB write directly to `\\192.168.214.131\dguh` (root/Banana1717) from a LAN machine, or scp to proxmox3 (`/mnt/dguh/videos/filmnoir/...`).
