# Headless / LXC / SSH Spotify OAuth Setup

When Hermes runs in a container, LXC, or remote SSH session, the interactive
`hermes auth spotify` command hangs because it starts a local HTTP callback
server on `127.0.0.1:43827` that the user's desktop browser can't reach.

This reference documents the **manual code-exchange flow**: generate the
authorize URL, have the user open it on their desktop, paste back the
callback URL, and complete the token exchange.

## Flow Overview

```
Agent (LXC)                           User (Desktop Browser)
    │                                        │
    ├── Generate PKCE challenge + state ─────┤
    │                                        │
    ├── Print authorize URL ─────────────────┤
    │                                        ├── Open URL, log into Spotify
    │                                        ├── Click Agree
    │                                        ├── Browser redirects to:
    │                                        │   http://127.0.0.1:43827/spotify/callback?code=X&state=Y
    │                                        │   (fails — no server on desktop)
    │                                        ├── User copies the failed URL
    │◄───────── Paste callback URL ──────────┤
    │                                        │
    ├── Extract `code` from callback URL ────┤
    ├── Exchange code + verifier for tokens ─┤
    ├── Save to ~/.hermes/auth.json ─────────┤
    │                                        │
    Spotify auth complete ✓                  │
```

## Step-by-step

### 1. Generate the Authorize URL

Run from the Hermes venv Python:

```python
import os, sys, uuid
sys.path.insert(0, '/usr/local/lib/hermes-agent')
from dotenv import load_dotenv
load_dotenv('/root/.hermes/.env', override=True)

from hermes_cli.auth import (
    _spotify_client_id, _spotify_redirect_uri, _spotify_scope_string,
    _spotify_code_verifier, _spotify_code_challenge,
    _spotify_build_authorize_url, _spotify_accounts_base_url,
    _spotify_api_base_url, get_provider_auth_state
)

state = get_provider_auth_state('spotify') or {}
client_id = _spotify_client_id(None, state)
redirect_uri = _spotify_redirect_uri(None, state)
scope = _spotify_scope_string(raw_scope=state.get('scope'))
accounts_url = _spotify_accounts_base_url(state)
code_verifier = _spotify_code_verifier()
code_challenge = _spotify_code_challenge(code_verifier)
state_nonce = uuid.uuid4().hex

auth_url = _spotify_build_authorize_url(
    client_id=client_id, redirect_uri=redirect_uri,
    scope=scope, state=state_nonce,
    code_challenge=code_challenge, accounts_base_url=accounts_url
)

print(f'Authorize URL:\n{auth_url}\n')
print(f'Code verifier: {code_verifier}')
print(f'State nonce: {state_nonce}')
```

### 2. User Opens URL in Desktop Browser

The user opens the `auth_url` in their browser, logs into Spotify, and clicks
**Agree**. Their browser redirects to:

```
http://127.0.0.1:43827/spotify/callback?code=AQ...&state=9d40...
```

This page will fail to load (no local server on their machine). **The user
copies the full URL from the address bar** and pastes it back to the agent.

### 3. Complete the Token Exchange

Extract the `code` from the callback URL and exchange it:

```python
from urllib.parse import urlparse, parse_qs

callback_url = "http://127.0.0.1:43827/spotify/callback?code=AQ...&state=9d40..."
parsed = urlparse(callback_url)
params = parse_qs(parsed.query)
auth_code = params['code'][0]
returned_state = params['state'][0]

# Verify state matches
assert returned_state == state_nonce, "State mismatch — possible CSRF"

# Exchange code for tokens
from hermes_cli.auth import _spotify_exchange_code_for_tokens
token_payload = _spotify_exchange_code_for_tokens(
    client_id=client_id,
    code=auth_code,
    redirect_uri=redirect_uri,
    code_verifier=code_verifier,
    accounts_base_url=accounts_url,
    timeout_seconds=20.0,
)

# Save to auth store
from hermes_cli.auth import (
    _spotify_token_payload_to_state, _load_auth_store,
    _store_provider_state, _save_auth_store, _auth_store_lock
)
spotify_state = _spotify_token_payload_to_state(
    token_payload, client_id=client_id, redirect_uri=redirect_uri,
    requested_scope=scope, accounts_base_url=accounts_url,
    api_base_url=_spotify_api_base_url(state),
)
with _auth_store_lock():
    auth_store = _load_auth_store()
    _store_provider_state(auth_store, "spotify", spotify_state, set_active=False)
    saved_to = _save_auth_store(auth_store)
    print(f"Saved to: {saved_to}")
```

### 4. Verify

```bash
hermes auth status spotify
# → "spotify: logged in"
```

## When to use SSH forwarding instead

If the user has SSH access to the host from their desktop:

```bash
ssh -L 43827:127.0.0.1:43827 user@host
# then in that SSH session:
hermes auth spotify
```

This forwards the callback port so the browser redirect reaches the Hermes
server. The code-exchange flow above is the fallback when SSH forwarding is
not available or the user prefers a copy-paste approach.

## Common failure modes

| Problem | Fix |
|---------|-----|
| `state` mismatch | Generate a fresh URL + nonce pair. The nonce is a one-time random value. |
| `invalid_grant` error on exchange | The `code` expires quickly (~1 minute). Generate a new URL and have the user re-authorize promptly. |
| Spotify redirects to a different port | The redirect URI must exactly match what's registered in the Spotify Developer App settings. Default: `http://127.0.0.1:43827/spotify/callback` |
| `401 Unauthorized` on first tool use | Token was saved but the toolset/gating needs a session restart. Try `hermes auth status spotify` to confirm token presence. |
| `spotify_playlists` etc. not found as commands | Spotify plugin is not enabled. Run `hermes plugins enable spotify` and start a new session. |

## Verified Working (July 2026)

This flow was tested and confirmed working on 7 July 2026:
1. Generated authorize URL with PKCE challenge + state nonce
2. User opened URL in desktop browser, logged into Spotify, clicked Agree
3. Browser redirected to `http://127.0.0.1:43827/spotify/callback?code=...&state=...` (page failed to load — expected)
4. User copied the failed URL from the address bar and pasted it back
5. Agent extracted the `code` parameter and exchanged it with `_spotify_exchange_code_for_tokens()`
6. Token payload saved to `/root/.hermes/auth.json` via `_save_auth_store()`
7. Verified with `hermes auth status spotify` — logged in with new expiry timestamp

Total round-trip time: ~30 seconds. The code verifier remained valid for the ~5 seconds between URL generation and callback paste.
