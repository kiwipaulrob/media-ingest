# Spotify API Playlist Creation (Fallback)

## When to Use This

The Hermes Spotify plugin (7 tools) is the primary interface. Use this API-based fallback when:

1. The Spotify plugin was just enabled but requires a **session restart** to take effect
2. You need to script bulk operations (50+ tracks) that would be slow tool-by-tool
3. You need fine-grained control over API responses

## Token Source

The access token lives in the auth store:

```python
import json
with open('/root/.hermes/auth.json') as f:
    auth = json.load(f)
access_token = auth['providers']['spotify']['access_token']
```

Check expiration: `auth['providers']['spotify']['expires_at']`. If expired, exchange the refresh token.

## Workflow

### 1. Get Current User

```python
import urllib.request, json
req = urllib.request.Request("https://api.spotify.com/v1/me",
    headers={"Authorization": f"Bearer {access_token}"})
with urllib.request.urlopen(req) as resp:
    user_id = json.loads(resp.read())['id']
```

### 2. Create Playlist

```python
data = json.dumps({"name": "Playlist Name", "description": "...", "public": False}).encode()
req = urllib.request.Request(
    f"https://api.spotify.com/v1/users/{user_id}/playlists",
    data=data, headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"})
with urllib.request.urlopen(req) as resp:
    playlist_id = json.loads(resp.read())['id']
```

### 3. Search for Each Track

```python
import urllib.parse
query = urllib.parse.quote(f"{artist} {title}")
req = urllib.request.Request(
    f"https://api.spotify.com/v1/search?q={query}&type=track&limit=3",
    headers={"Authorization": f"Bearer {access_token}"})
with urllib.request.urlopen(req, timeout=10) as resp:
    items = json.loads(resp.read()).get('tracks', {}).get('items', [])
    if items:
        best = items[0]
        for item in items:
            if any(artist.lower()[:10] in a['name'].lower() for a in item.get('artists',[])):
                best = item; break
        spotify_uri = best['uri']
```

### 4. Add Tracks (Up to 50 Per Call)

```python
for i in range(0, len(uris), 50):
    batch = uris[i:i+50]
    data = json.dumps({"uris": batch}).encode()
    req = urllib.request.Request(
        f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
        data=data, method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"Added batch: {json.loads(resp.read()).get('snapshot_id','?')[:20]}...")
```

### 5. Remove Wrong Tracks

```python
data = json.dumps({"tracks": [{"uri": "spotify:track:..."}]}).encode()
req = urllib.request.Request(
    f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
    data=data, method="DELETE",
    headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as resp:
    result = json.loads(resp.read())
```

### 6. Verify Playlist

```python
req = urllib.request.Request(
    f"https://api.spotify.com/v1/playlists/{playlist_id}?fields=tracks(total)",
    headers={"Authorization": f"Bearer {access_token}"})
with urllib.request.urlopen(req) as resp:
    print(f"Total tracks: {json.loads(resp.read())['tracks']['total']}")
```

## Niche Track Handling

| Result | % | Action |
|--------|:-:|--------|
| Exact match | ~60% | Add directly |
| Same artist, wrong title | ~25% | Accept; note for user review |
| Same title, wrong artist | ~5% | Reject |
| Not on Spotify | ~10% | Skip; report to user |

## Error Handling

### 502 Bad Gateway (DELETE operations)
Retry once after 1s. If it persists, the request will eventually succeed.

### 403 Forbidden
- **No active device:** Not relevant for playlist ops (only playback)
- **Premium required:** Not relevant for playlist ops
- **Rate limited (429):** Slow down and retry

## URI Formats

- **URI:** `spotify:track:0DiWol3AO6WpXZgp0goxAV` (preferred)
- **URL:** `https://open.spotify.com/track/0DiWol3AO6WpXZgp0goxAV`
- **Bare ID:** `0DiWol3AO6WpXZgp0goxAV`

Search results return URIs in the `uri` field — pass these directly.
