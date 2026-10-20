"""media_lib — tested, modular building blocks for Jellyfin media ingest.

Modules:
    config   - all paths, thresholds and the library map in one place
    matcher  - release-name -> library-item matching (guards + two fixes)
    safety   - lockfile, prefix-normalised ledger, byte-stable check list
    jellyfin - REST client (env-based creds, no hardcoded user ids)
    probe    - container/poison detection + ffprobe over ssh
    tmdb     - TMDB search + identity gate
"""

__all__ = ['config', 'matcher', 'safety', 'jellyfin', 'probe', 'tmdb']
__version__ = '0.1.0'
