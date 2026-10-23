"""Jellyfin REST client.

Two rules learned the hard way, both encoded here rather than in prose:

1. **An empty response body is a busy server, not a bad query.** While a scan
   or refresh backlog drains, /Items calls intermittently return zero bytes and
   a naive parser dies with JSONDecodeError, which reads as "no items".
2. **A partial payload 400s.** /Items/{id} wants the whole DTO back; a minimal
   retry is the fallback, and the 400 BODY names the failing field.

Credentials resolve from the process environment FIRST (so a Hermes plugin
gets its own scoped env), then from the shared .env file (so a cron CLI works
without a shell wrapper). The admin user id is never hardcoded: four scripts
used to carry the same literal id, so a re-created user silently broke all four.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ENV_FILE = Path("/root/.hermes/.env")
_RETRIES = 5
_RETRY_SLEEP = 4


class JellyfinError(RuntimeError):
    """Raised when the server answers with an error we cannot retry past."""


def load_env_file(path: Path = ENV_FILE) -> Dict[str, str]:
    """Minimal .env reader: KEY=VALUE, ignores comments and blanks."""
    out: Dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Process env wins; the shared .env file is the fallback."""
    if os.environ.get(name):
        return os.environ[name]
    return os.environ.get(name) or load_env_file().get(name, default)


def base_url() -> str:
    url = env("JELLYFIN_URL")
    if not url:
        raise JellyfinError("JELLYFIN_URL is not set")
    return url.rstrip("/")


def api_key() -> str:
    key = env("JELLYFIN_API_KEY")
    if not key:
        raise JellyfinError("JELLYFIN_API_KEY is not set")
    return key


def user_id() -> str:
    """Admin user id used for user-scoped item reads.

    Deliberately NOT hardcoded: `GET /Items/{id}` 400s on this build, and every
    user-scoped route needs this id, so a stale literal breaks every caller at
    once with a confusing error.
    """
    uid = env("JELLYFIN_USER_ID")
    if not uid:
        raise JellyfinError(
            "JELLYFIN_USER_ID is not set. Set it in the environment or in "
            f"{ENV_FILE} (get it from GET /Users)."
        )
    return uid


def auth_header(key: Optional[str] = None) -> Dict[str, str]:
    return {"Authorization": f'MediaBrowser Token="{key or api_key()}"'}


def coerce_bio(value: Any) -> str:
    """Flatten a (text, thumb) tuple or None into a plain string.

    A batched Wikipedia lookup returns a tuple; assigning it to Overview
    serialises a JSON array where the server expects a string and every such
    write 400s with 'JsonStringConverter read too much or not enough'.
    """
    if isinstance(value, (tuple, list)):
        value = value[0] if value else ""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


# ---------------------------------------------------------------- transport
def _request(
    path: str,
    *,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    key: Optional[str] = None,
    timeout: int = 60,
) -> str:
    """One HTTP call. Returns the raw text (possibly empty)."""
    url = path if path.startswith("http") else base_url() + path
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(auth_header(key))
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise JellyfinError(f"{method} {path} -> {exc.code} {detail}") from exc


def get_bytes(path: str, timeout: int = 60) -> bytes:
    """GET raw bytes (images). Used to byte-verify an upload actually landed."""
    req = urllib.request.Request(base_url() + path, headers=dict(auth_header()))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def get_json(
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    key: Optional[str] = None,
    retries: int = _RETRIES,
    timeout: int = 60,
) -> Dict[str, Any]:
    """GET with the empty-body retry. Returns {} only after exhausting retries."""
    if params:
        path = f"{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            text = _request(path, key=key, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(_RETRY_SLEEP)
            continue
        if text.strip():
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                time.sleep(_RETRY_SLEEP)
                continue
        time.sleep(_RETRY_SLEEP)  # empty body == busy server, retry
    return {}


def post_item(item_id: str, body: Dict[str, Any], *, key: Optional[str] = None) -> int:
    """POST an item DTO. Falls back to a minimal body on 400.

    The minimal retry exists because the full DTO carries fields some updaters
    reject (Genres on items; unrelated keys on Person rows). Losing a whole
    write to an unrelated field would be a silent metadata loss.
    """
    try:
        _request(f"/Items/{item_id}", method="POST", body=body, key=key)
        return 204
    except JellyfinError as exc:
        if "-> 400" not in str(exc):
            raise
        minimal: dict[str, Any] = {
            k: body[k] for k in ("Id", "Name", "Overview", "LockedFields") if k in body
        }
        if not minimal:
            raise
        _request(f"/Items/{item_id}", method="POST", body=minimal, key=key)
        return 204


def _fields_param(fields: Optional[Iterable[str]]) -> Optional[str]:
    """Normalise a fields argument to a comma list.

    A STRING must pass through unchanged: ','.join('Path,MediaSources') yields
    'P,a,t,h,...', which the server accepts and then returns no Path at all —
    a silent empty-field bug that reads as "library has no paths".
    """
    if not fields:
        return None
    if isinstance(fields, str):
        return fields
    return ",".join(fields)


def item(
    uid: str,
    item_id: str,
    fields: Optional[Iterable[str]] = None,
    key: Optional[str] = None,
) -> Dict[str, Any]:
    """User-scoped single-item read (the only route that works on this build)."""
    params = {"Fields": _fields_param(fields)} if fields else None
    return get_json(f"/Users/{uid}/Items/{item_id}", params=params, key=key)


def list_items(
    uid: str,
    *,
    recursive: bool = True,
    include_types: Optional[str] = None,
    parent_id: Optional[str] = None,
    fields: Optional[Iterable[str]] = None,
    limit: int = 500,
    key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Paginated item listing.

    Limit=0 returns ZERO items on this build — it is not an 'unlimited'
    sentinel — so paging is explicit and bounded here.
    """
    out: List[Dict[str, Any]] = []
    start = 0
    while True:
        params: Dict[str, Any] = {"StartIndex": start, "Limit": limit}
        if recursive:
            params["Recursive"] = "true"
        if include_types:
            params["IncludeItemTypes"] = include_types
        if parent_id:
            params["ParentId"] = parent_id
        if fields:
            params["Fields"] = _fields_param(fields)
        page = get_json(f"/Users/{uid}/Items", params=params, key=key)
        rows = page.get("Items") or []
        if not rows:
            break
        out.extend(rows)
        start += len(rows)
        if start >= (page.get("TotalRecordCount") or 0):
            break
    return out


def refresh_library(lib_id: str) -> int:
    """Library-scoped Refresh — the ONLY sanctioned rescan form.

    Bare `?Recursive=true` plus both ReplaceAll flags false, with NO mode
    params: this build 400s when MetadataRefreshMode/ImageRefreshMode are
    passed to the library-level route, and a full library rescan would purge
    the ghost rows that are deliberately kept.
    """
    params = {
        "Recursive": "true",
        "ReplaceAllMetadata": "false",
        "ReplaceAllImages": "false",
    }
    _request(f"/Items/{lib_id}/Refresh?{urllib.parse.urlencode(params)}", method="POST")
    return 204


def refresh_item(item_id: str, *, metadata: str = "FullRefresh",
                 images: str = "FullRefresh",
                 replace_all_metadata: bool = False,
                 replace_all_images: bool = False) -> int:
    """Per-item Refresh — the only way to make a known row RE-PROBE its file.

    A library-scoped Refresh re-resolves metadata for known rows but does not
    re-probe the media, so after a file is swapped in place at the same path the
    row keeps reporting the OLD stream info (codec, interlaced flag, bitrate).
    Disk is ground truth; this forces the re-read.

    ReplaceAll flags default to false: turning them on would overwrite hand-set
    fields. Note this route (unlike the library-level one) accepts the mode
    params — the library route 400s when they are passed.
    """
    params = {
        "MetadataRefreshMode": metadata,
        "ImageRefreshMode": images,
        "ReplaceAllMetadata": "true" if replace_all_metadata else "false",
        "ReplaceAllImages": "true" if replace_all_images else "false",
    }
    _request(f"/Items/{item_id}/Refresh?{urllib.parse.urlencode(params)}", method="POST")
    return 204


def set_primary_image(item_id: str, data: bytes, content_type: str = "image/jpeg") -> int:
    """Upload an item's Primary image.

    The controller reads the request body through a Base64 CryptoStream, so RAW
    BYTES 500 with a ``FormatException`` from ``FromBase64Transform`` — the body
    must be the base64 TEXT of the image. That failure reads as a broken endpoint
    when it is only an encoding mistake, so it is encoded here, once.
    """
    import base64 as _b64

    body = _b64.b64encode(data)
    headers = dict(auth_header())
    headers["Content-Type"] = content_type
    req = urllib.request.Request(
        base_url() + f"/Items/{item_id}/Images/Primary",
        data=body, headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.status


def delete_item(item_id: str) -> int:
    """DELETE an item.

    The server unlinks the item's file. For a FOLDER-shaped item it can take the
    whole folder with it, so only call this for a single-file row whose path has
    been verified.
    """
    _request(f"/Items/{item_id}", method="DELETE")
    return 204


def notify_media(paths: Iterable[str], update_type: str = "Created") -> int:
    """Tell the scanner that files appeared.

    A library-scoped Refresh re-resolves metadata for KNOWN rows and does NOT
    discover new files, so a placement that only refreshes sits invisible until
    the next full discovery. This is the surgical route that actually adds them.
    A file already queued for refresh makes this a no-op, not an error.
    """
    body = {"Updates": [{"Path": p, "UpdateType": update_type} for p in paths]}
    _request("/Library/Media/Updated", method="POST", body=body)
    return 204


def user_data(uid: str, item_id: str, *, key: Optional[str] = None) -> Dict[str, Any]:
    """Current UserData for one item: Played, PlayCount, PlaybackPositionTicks."""
    return get_json(f"/Users/{uid}/Items/{item_id}/UserData", key=key)


def set_played(uid: str, item_id: str, played: bool = True, *,
               key: Optional[str] = None) -> int:
    """Mark an item played (POST) or unplayed (DELETE).

    Played is a separate endpoint from UserData: posting UserData will NOT set it.
    """
    path = f"/Users/{uid}/PlayedItems/{item_id}"
    _request(path, method="POST" if played else "DELETE",
             body={} if played else None, key=key)
    return 204


def set_playback_position(uid: str, item_id: str, ticks: int, *,
                          key: Optional[str] = None) -> int:
    """Restore a resume point. 0 ticks clears it."""
    _request(f"/Users/{uid}/Items/{item_id}/UserData", method="POST",
             body={"PlaybackPositionTicks": int(ticks)}, key=key)
    return 204
