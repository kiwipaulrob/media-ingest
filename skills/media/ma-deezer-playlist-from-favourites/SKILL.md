---
name: ma-deezer-playlist-from-favourites
description: "Build Deezer playlists from MA favourites."
version: 1.0.0
author: Hermes Agent
---

# MA Deezer Playlist from Favourites

Build a playlist of your MA favourited tracks that are **available on Deezer**, plus a companion playlist of tracks **not on Deezer** using their original sources.

## Overview

Music Assistant's "All favorited tracks" builtin playlist (ID=1) aggregates favourites across all providers. This workflow:

1. **Exports** the favourites M3U8 via MA API
2. **Searches** Deezer's free public API for each track (no API key needed)
3. **Matches** tracks using fuzzy title/artist comparison
4. **Imports** matched Deezer tracks into new MA playlists using `import_playlist`
5. **Optionally** creates a companion "Not on Deezer" playlist using the original provider URIs

## Prerequisites

- MA_TOKEN set in `source /root/.bashrc`
- Deezer provider configured in MA (enabled)
- Network access to `api.deezer.com` (public, free, no key)

## Scripts

Three scripts are created under `/root/.hermes/scripts/`:

| Script | Purpose |
|---|---|
| `ma-deezer-favs-playlist-v2.py` | Full pipeline: parse favs → search Deezer → import matched → save results |
| `ma-deezer-importer-v2.py` | Import pre-matched Deezer tracks (from saved JSON) into MA in chunks |
| `ma-not-on-deezer.py` | Build companion playlist of tracks NOT found on Deezer, using original provider URIs |

Data files:
- `/root/.hermes/data/ma-deezer-favs-match.json` — saved Deezer match results (reusable)

## Key Techniques

### Deezer Search

```python
# Deezer public API — no auth needed
url = f"https://api.deezer.com/search?q={urllib.parse.quote(query)}&limit=5"
# Returns {data: [{id, title, artist: {name}, ...}]}
```

### Matching Logic

- Normalise both titles: lowercase, strip punctuation, remove `(Remastered)` suffixes
- Strip `feat.` clauses from titles
- Compare artist names (check if query artist is in Deezer artist name or vice versa)
- Score: title_similarity × (1.0 if artist match, 0.5 if not)
- Threshold: ≥0.7 to accept

### MA Import (the reliable way)

```python
result = api("music/playlists/import_playlist", {
    "m3u_data": m3u_string,
    "library_matching": True,      # resolve providers
    "match_providers": ["deezer"], # limit which providers to search
})
```

**IMPORTANT:** `import_playlist` works reliably. `add_playlist_tracks` creates background tasks that silently fail — avoid it.

### Track Counting

Count `#EXTMA:` lines in the exported M3U8 — NOT `#EXTINF:` lines. MA sometimes omits `#EXTINF:` for `builtin://` URIs.

```python
count = m3u8_string.count("#EXTMA:")  # correct
# NOT: m3u8_string.count("#EXTINF:")  # unreliable
```

## Pitfalls

- **Large imports fail silently.** Always chunk into batches of **100 tracks** max.
- **Create and populate in the same script run.** MA's `add_playlist_tracks` background task silently fails if the playlist was created in a different script.
- **`import_playlist` is the only reliable method** for populating new playlists.
- **Wait 10-15 seconds between chunks** to avoid overwhelming MA's background task queue.
- **`music/playlists/remove` returns "Internal server error"** on success — catch and handle this.
- **Deezer hit rate is ~50%** for indie/alternative-heavy libraries. Many local/NZ/obscure tracks won't be on Deezer.
- **Parallelise Deezer searches** with ThreadPoolExecutor (8 workers works well, 0.15s delay per worker = ~14 tracks/s).

## Full Pipeline (run once)

```bash
source /root/.bashrc

# Step 1: Search Deezer + import Deezer tracks
python3 /root/.hermes/scripts/ma-deezer-favs-playlist-v2.py

# Step 2: Import "not on Deezer" companion playlist
python3 /root/.hermes/scripts/ma-not-on-deezer.py
```

## Re-running (new favourites added)

If you've favourited more tracks since last run:

1. Re-run `ma-deezer-favs-playlist-v2.py` to search Deezer for the new tracks
2. It will re-process all 1,915+ tracks (Deezer API is fast enough, ~127s)
3. Delete old chunk playlists first if you want a clean replacement
