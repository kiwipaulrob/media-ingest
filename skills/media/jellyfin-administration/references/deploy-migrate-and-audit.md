# Deploy, migrate & library audit

Topology, path mapping, version/upgrade rules, the DB-migration wedge and its pitfalls, the post-move checklist, and the ghost-item audit.

## Version numbering & upgrades (Sep 2026)

- Numbering changed: the "10.x" prefix is gone. Feature releases bump the FIRST number — the 10.12-equivalent shipped as **12.0** (8 Sep 2026), followed by **12.1** (15 Sep 2026); the next feature release is **13.0** (State of the Fin 2026-05 names it as the scanner-rewrite target; mapping is old-minor → new-major, i.e. 10.13 → 13.0). Patches continue as 12.x.y. This install is on **10.11.11** — a direct 10.11.x → 12.x upgrade is supported.
- **12.x upgrade rules:** STOP + full manual backup first (first boot runs one-way DB migrations; `--mode MigrateSystem` runs them without starting the server); prove the backup archive lists cleanly (`tar -tzf` must exit 0) BEFORE stopping or recreating the old container — a corrupt 18 GB tarball discovered after the container is gone is unrecoverable; remove third-party plugins first (the server targets .NET 10, so 10.11 builds won't load); a FULL library scan is REQUIRED afterwards, and the migration path-checks every item against disk — **it will purge this user's deliberately-kept ghost rows, so export the ghost list (Id/Type/Name/Path) BEFORE upgrading.** Security fixes are included. **12.x additionally:** the `/emby/` and `/mediabrowser/` compatibility routes are REMOVED and deprecated sign-in auth is disabled — audit connected clients first, because stale app versions stop working; auto-grouped alternate versions are cleared by the upgrade and only return after the full scan, so multipart stacks read as missing until then; usernames differing only by case fail migration — check `GET /Users` for collisions first. Size the backup before taking it (config runs ~28 GB of metadata + data: stage it off-box, never on the CT's own root disk). The post-upgrade full scan is the heaviest sustained read the server ever performs — gate it on source-disk health, and wait out recent ATA errors first. The compose image tag is `jellyfin/jellyfin:latest`, so any plain `pull && up -d` jumps major versions unasked — confirm the resolved tag before pulling. Recreate from a `docker inspect` dump of the live container (mounts, `--net host`, `--device /dev/dri/renderD128`, env) with the image pinned to the exact target tag (`jellyfin/jellyfin:12.1`, never `:latest`), and rename the stopped old container (`jellyfin-10.11.11`) instead of removing it — one rename + start is an instant rollback for the settling-in period; remove it only on explicit user approval days later. Verify after boot: `System/Info` version, all libraries with locations, then spot-check two recently-touched items' `MediaSources` streams before calling the upgrade done.
- **Cadence** (GitHub release history): feature-to-feature gaps ran 168/359/323 days (median ~10.6 mo) for 10.9→10.10→10.11→12.0, so 13.0 lands roughly ~mid-2027; the first patch after a `.0` lands fast (+2 to +19 days historically), then patches every ~1-6 weeks.
- The **"Jellyfin release watcher" cron** (no_agent script `~/.hermes/scripts/jellyfin_release_watch.py`, state in `~/.hermes/state/jellyfin_release_notified.txt`) emails Paul once per new stable release > 12.0. Retarget to 13.0-only by changing `MIN_TARGET` in the script.

## Deployment pattern (this homelab)

- **Host**: Proxmox LXC (CT105, unprivileged, Debian 12) with Docker + compose inside.
- **Media**: CIFS shares from proxmox3/4 mounted on the host (`/srv/files/<share>`), bind-mounted into the LXC (`mp0: /srv/files/dguh,mp=/mnt/media`), then bind-mounted into the container. **This chain is read-WRITE and in-Jellyfin deletion is enabled** — see SKILL.md, "Enabling real in-Jellyfin media deletion". Older notes describing it as read-only are stale: a `ro` reintroduced at ANY of the four hops (fstab → CT restart → compose volume → `docker compose up -d`) is what silently re-breaks deletion, and fixing one hop in isolation looks like the fix "didn't work".
- **GPU**: pass `/dev/dri/renderD128` through LXC config AND compose `devices:`; verify the log line `VAAPI device "/dev/dri/renderD128" supports Vulkan DRM interop` at startup.
- **RAM-disk transcodes (tmpfs).** Segments are ephemeral and self-cleaning, so a 1 GB tmpfs is safe on a 3 GB CT (sized for one concurrent stream; two is the risk zone). Encoding settings are NOT in the REST API (`/System/Configuration` carries no encoding keys) — edit `/opt/jellyfin/config/config/encoding.xml` directly: `TranscodingTempPath` → `/transcodes`, plus `EnableThrottling=true` and `EnableSegmentDeletion=true` to bound how far ahead it transcodes and clean watched segments (both default off, and both are what keep a 1 GB disk from filling). Compose: `tmpfs: [/transcodes:size=1G,mode=1777]`, back up both files first, `docker compose up -d`, then verify `mount | grep transcodes` inside the container and a clean boot (a bad path logs errors; rebooting just empties the disk). Check `encoding.xml` for existing VAAPI/QSV + `EnableHardwareEncoding` BEFORE changing anything — the device passthrough may already be live, in which case there is nothing to enable.
- **Transcode scope**: this user runs films-only (no music library access). Honour that when deciding what to migrate.

## Library path mapping (CRITICAL)

The DB stores media items at the *old container's* internal roots (e.g. `/data/movies/...`, `/data/tvshows/...`). On the new host you must reproduce those exact paths with compose bind maps, e.g. `/mnt/media/videos/Films:/data/movies:ro`. Get the mapping wrong and every item points at nothing.

**The container names are NOT the disk directory names — three of seven differ, so never infer the disk path from the library name.** Read the real chain from the container itself (`docker inspect <container> --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'`) rather than guessing:

| Jellyfin (in container) | Disk (proxmox3 `/mnt/dguh/videos/`) |
|---|---|
| `/data/musicdocos-series` | `musicdocos-series` |
| `/data/documentaries` | `Documentaries` |
| `/data/movies` | **`Films`** |
| `/data/filmnoir` | `filmnoir` |
| `/data/tvshows` | **`TV`** |
| `/data/musicdocos` | `musicdocos` |
| `/data/filmdocos` | **`FilmDocumentaries`** |

Only `/data/...` prefixes are translatable. An item whose `Path` falls outside them has no on-disk location you can act on, so resolve the prefix **before** planning any copy, rename or replacement — and treat an unresolvable path as a refusal, not as licence to guess a nearby directory.

Do NOT rewrite paths in the DB unless you must (e.g. film-noir category collapse). Path rewrite in SQLite is viable but the filmnoir nested-folder case needed care; prefer matching the compose map to the DB paths. (The one sanctioned exception is the single-row container-lie swap — see `references/playback-and-repair.md`.)

## Adding a new Movies-type library

All film libraries on this install are `CollectionType=movies` (only Music Docos Series is `tvshows`), so a new section is a movies library with its own bind root. Order matters:

1. `mkdir` natively on the SMB server (`proxmox3:/mnt/dguh/videos/<NewDir>/`) — never through the read-only prox1 view.
2. Confirm the CT sees it (`pct exec 105 -- ls -ld /mnt/media/videos/<NewDir>`); the CIFS bind covers the whole share, so no CT change is needed.
3. Add the compose bind (`/mnt/media/videos/<NewDir>:/data/<newroot>:rw`), back up the compose file first, then `docker compose up -d` (recreates the container) and wait for `GET /health` → `Healthy`.
4. Create the library: `POST /Library/VirtualFolders?collectionType=movies&paths=/data/<newroot>&name=<Name>&refreshLibrary=true` (204).
5. Move titles with hard links (same share, same inode, zero extra disk), then scan and verify each new row (stacking via `PartCount`, metadata via the 4-field gate in `references/ingest-and-intake.md`) BEFORE touching the source entries.

**New-library pickup notes:** a brand-new empty library logs `"Library folder ... is inaccessible or empty, skipping"` — that warning is expected until files land, not a fault. A library-scoped Refresh alone may not index a never-scanned library; the first pickup can require the Scan Media Library scheduled task, which scans ALL libraries. Before any full scan, export insurance: per-library row counts plus `Id/Type/Name/Path` for every row, then re-verify counts and spot-check named ghosts afterwards (a plain scan preserved all ghost rows on this install — treat that as luck to verify, not license to skip the export).

## The 10.9 → 10.11 DB migration wedge

Full case and prune recipe: `references/jellyfin-db-migration-10-11.md`.

- **Symptom:** server boots, logs `MigrateLibraryDb: Migrating the userdata from library.db may take a while`, then runs 2+ hours at ~50% CPU with the API down; the container looks "healthy" to docker but health checks stop logging.
- **Root cause on this install:** a 1.6 GB `library.db` with **463K items, of which ~390K (84%) were music** (Audio/MusicArtist/MusicAlbum/MusicGenre). EF migrates every row in ONE uncommitted transaction; the working set exceeds a 2-core CT's RAM cap and .NET GC-thrashes forever.
- **Fix: prune BEFORE migrating.** Remove music (or any unwanted library type) rows from the *copied* `library.db`, clean orphaned link rows, discard the wedged WAL, restart. Migration then completes in seconds.

### Diagnosis — working or wedged?

- **Progress meter = WAL growth**, not log lines. `stat -c%s jellyfin.db-wal` twice 3-5 min apart: 0 KB/s while CPU is pegged = wedged (GC-thrash), not working.
- `BaseItems` count 0 in the target DB means the giant transaction never committed — killing it loses ZERO progress and is safe.
- When the CT is CPU-starved, `pct exec` hangs. Probe SQLite from the host rootfs instead: `sqlite3 "file:/var/lib/lxc/<id>/rootfs/opt/jellyfin/config/data/jellyfin.db?mode=ro"`.
- Log silence + Kestrel heartbeat warnings + resumed health checks later = thread-pool starvation windows around the big save.
- GC-thrash signature: hot `.NET` threads, VmRSS at cap + VmSwap in the hundreds of MB, host load average inflated.
- A RAM bump (4G→6G) helps marginally but is NOT the fix when the row count is the problem. Reduce rows.

## Pitfalls

**tar ownership clash after an appdata move.** Extracting appdata as root (`pct exec tar`) over a compose-prepared config tree leaves root-owned files the container user cannot write — crash loop with `UnauthorizedAccessException: ... plugins/<Name>/meta.json is denied`. Fix: with the container stopped, `chown -R 100000:100000` the config+cache dirs *from the host* (unprivileged LXC: host uid 100000 = CT root = container user). Then `docker compose up -d`.

**Media mounts appear empty right after CT boot.** CIFS bind mounts inside the LXC take a few seconds to remount at CT start; docker can start before them and create empty `/data/*` dirs. Fix: `docker compose down && up -d` after the mount is live, then verify item counts per library.

**LXC fails to start or hangs at start — suspect the CIFS source before the container.** When the LXC's media comes from CIFS bind mounts, a start failure (`pve-container@<id>.service: Main process exited, status=1/FAILURE`) or a foreground `lxc-start -n <id> -F` hanging past 90s usually means the CIFS *source host* is unresponsive, not that the container config broke — nothing inside the guest changed. Diagnose outside-in: `journalctl -u pve-container@<id>.service` for the transition timestamps, then check the source host's load (`uptime`; look for ffmpeg remuxes, dedup scans, `du` walks over multi-TB trees) and write-probe the share. A recovered source boots the unchanged container clean in seconds — that clean boot is the proof the fault was never in Jellyfin. **Moving the guest to a bigger host does NOT fix this class of hang** — the new guest reads the same CIFS source and stalls identically. Sequence heavy source-host jobs (remuxes, multi-TB rsyncs, full-tree `du`) away from active download/transcode windows sharing the same spindle.

## Verification checklist after a move

1. `docker ps` → `Up ... (healthy)`; API `System/Info/Public` returns `serverName` + `version` + `startupWizardCompleted: true`.
2. Users preserved: `SELECT Username FROM Users` in `jellyfin.db` (10.11 schema — no `IsDisabled` column; it has `InternalId`, `EnableAutoLogin`).
3. Item count sanity: `SELECT COUNT(*) FROM BaseItems` matches the pruned source count.
4. Watch state: `SELECT COUNT(*) FROM UserData` non-zero.
5. Libraries: CollectionFolder rows carry *virtual* paths `/config/root/default/<Name>` in BOTH old and new DBs — identical shape old-vs-new proves faithful migration; the real roots live in the compose bind map + LibraryMonitor "Watching directory"/"Skipping ... path does not exist" log lines.
6. Media readable through the container: `docker exec jellyfin dd if=/data/movies/<file> bs=1M count=1 | wc -c` > 0.
7. Errors after restart: grep the log for `[ERR]`/`[FTL]` since the last clean start.
8. Artwork preserved across a storage rebind (same `/data/*` roots, so item rows survive — images must too): BEFORE the move, snapshot per library `{itemId: hasPrimaryImage}` via `/Users/{adminId}/Items?ParentId={libId}&Recursive=true&Fields=Path,ImageTags`; AFTER the rebind + refresh, compare by item Id — LOST must be 0. Copy non-video sidecars (`.nfo`/`.jpg`/`.srt`) natively alongside the media, and confirm `SaveLocalMetadata=false` on each library first so a refresh cannot overwrite local art.

## Library content audit — ghost items after a move/merge

After migrating, DB items can point at files that do not exist (content never copied off the old NAS) or at old release-style names (`Alphaville.1965.FRENCH.1080p.BluRay.x265-VXT.mp4`) while files were renamed clean. Audit DB Paths vs physical files.

- Empty dirs on BOTH source and destination predate the migration and are ghosts that can never play — encoder fixes will not help them.
- Keyword filename searches also catch release-group tags — a `-Noir` / `{1337x}-Noir` suffix marks the pirate group, not the genre; confirm a hit against its DB row before treating it as library content.
- Repeated `Could not find file ... folder.jpg/backdrop.jpg` lines for stale nested image paths are cosmetic artwork 404s — they never hang the server, so do not chase them as a hang cause.
- **Audit from the host SMB mount path, not inside the LXC:** media bind mounts exist only in the docker namespace (`docker exec`), not in the CT namespace (`pct exec`).

### Ghost taxonomy

Query `Type` as well as `Path`:

- A `Folder`-type row with NULL Size and zero top-level files on disk is a **fully-empty ghost**.
- A `Folder` row whose dir holds only `extras/`/`featurettes/`/`interviews/`/`trailers/` subdirs is an **extras-only ghost**, whose main-film `Movie` row is the missing piece.

Count top-level files per dir (`find <dir> -maxdepth 1 -type f | wc -l`) alongside the DB query (`SELECT Id,Type,Name,Path,Size FROM BaseItems WHERE Path LIKE '/data/<lib>%'`) so the report separates fully-empty from extras-only instead of lumping both as "empty".

### Chosen remediation (this user, Sep 2026)

**Rename disk files to the DB-expected release names — never rewrite the DB, and never run a library rescan.** The user keeps ghost entries as an explicit "films I need to replace" list, so a rescan (which purges them) is off-limits.

- Do the renames on the SMB server natively (`/mnt/<share>/videos/...` on proxmox3) — the prox1 `/srv/files/...` view is a read-only CIFS mount and renames fail there.
- `mv` per dir to the exact DB basename (release-style names, e.g. FileBrowser download timestamps like `2025-03-16-05-39_265132508.mp4` renamed to the release title).
- If one disk file maps to two DB rows (main film + extra), rename to the main row and leave the extra as a ghost.
- Then verify DB-path resolution from the host read-only mount, and confirm playback via the API.
- Drop replacement files into the empty ghost dirs later, name-matched to the DB entries, and they light up without any rescan (see `references/ingest-and-intake.md`).

## Cleanup notes for this install

- ✅ `db1/videos` DELETED 6 Sep 2026 (user-approved) after full-file md5 proved all 15 3JY-unique files byte-identical to GUH copies — 3JY freed 3.6T→2.9T used (~784G reclaimed); GUH untouched (5,168 files). The delete required `mount -o remount,rw /mnt/db1` first (fstab pins db1 `ro`; dguh is `rw`). Full record in `references/guh-3jy-videos-audit-2026-09.md`, including the 16 film-noir ghost entries (empty dirs + DB-expected exact filenames) awaiting replacement files.
- The Music library row still shows in the dashboard (no `/data/music` mount); delete via Dashboard → Libraries once logged in.
- CT102 rollback (6G alloc, stopped) frees RAM on prox1 when destroyed.
