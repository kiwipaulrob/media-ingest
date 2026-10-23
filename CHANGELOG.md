# Changelog

Notable changes to the media-ingest plugin and the operational scripts that
support it. Versions track `plugins/media-ingest/plugin.yaml`.

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
