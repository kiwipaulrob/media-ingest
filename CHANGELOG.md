# Changelog

Notable changes to the media-ingest plugin and the operational scripts that
support it. Versions track `plugins/media-ingest/plugin.yaml`.

## 0.3.0 — 2026-10-23

The Tdarr operations became agent-facing tools, after a review pass.

### Added
- Plugin tools `media_recode_status`, `media_recode_scan`, `media_recode_start`
  and `media_recode_prune`.
- `scripts/tdarr_scan.py` — scans one library with its OWN folder and refuses a
  parent path. A parent path once queued 1,266 files from other libraries, and
  two concurrent scans cross-assign rows; both are now impossible by construction.
- `tests/test_tdarr_scripts.py` (11 tests) covering the midnight rollover, scan
  scope guards, scanner-status parsing and watch-state keying.

### Fixed
- `open_window` only opened the CURRENT day's hour blocks, so an overnight run
  started at 22:00 opened nothing after 23:00. It now walks across midnight.
- `tdarr_scan` only watched its own library's scanner; it now refuses to start
  while EITHER library is scanning.
- `post_ingest_sweep.py` treated two failed `stat` calls (empty or error strings)
  as byte-identical and would delete a copy. It now requires numeric sizes first.
- `tdarr_scan` reported "Found: N Checking:0" as idle. That is the transient
  pre-extraction state, so a second scan could start on top of a running one.
- Duplicate imports and dead variables introduced by the API-key patch; stale
  comment in `verify_queued.py` claiming it shared `media_lib.qb` when it has its
  own qBittorrent helpers.

### Changed
- `ruff` is clean across `media_lib`, `cli`, `scripts` and `plugins`.

## 0.2.0 — 2026-10-23

Tdarr became the only sanctioned transcoder, and the tooling around it was
rebuilt after several silent failures.

### Added
- Tdarr cluster support: `scripts/tdarr_enable.py`, `tdarr_attach_libraries.py`,
  `tdarr_node_enable.py`, `tdarr_kick.py`, `tdarr_status.py`,
  `tdarr_preserve_userdata.py`, `tdarr_prune_foreign.py`.
- `scripts/jellyfin_dead_rows.py` — dead rows, generic item names, and media
  mis-nested inside a `VIDEO_TS` tree. Tests existence rather than inferring it.
- `media_lib/jellyfin.py`: `user_data`, `set_played`, `set_playback_position`,
  `refresh_item`, `delete_item`, `set_primary_image`, `get_bytes`.
- `media_lib/avsync.py` and `scripts/fix_av_offset.py` — A/V start-time checks.

### Changed
- Tdarr is the default and only recoder; the skill opens with that standing order.
- Skills and the plugin remain symlinked from the profile, so the local checkout
  is production and GitHub is the mirror.

### Fixed
- Jellyfin watch state is preserved across a container change (`X.avi` -> `X.mkv`
  creates a new item and drops the old one; snapshot/restore keys on the path
  without its extension).
- `scan-files` is only ever called with a library's own folder, one library at a
  time. A parent path indexed 1,266 files from other libraries.
- Secrets are read from the environment. A Tdarr API key had been committed to
  this public repository and therefore also needed rotating.

### Security
- `TDARR_API_KEY` is read via `media_lib.jellyfin.env`; scripts refuse to run
  without it. No credential is ever inlined.
