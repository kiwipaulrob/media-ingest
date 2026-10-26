"""HTTP cache + per-provider rate limiting.

Two lessons are encoded here:

* **Wikimedia throttles by IP, not by token.** A heavy pass blocks the whole
  host for the reset window, and retries make it worse. So the limiter is
  per-provider and the cache is on disk: a repeated sweep costs nothing.
* **A provider that fails must not look like a provider with no results.**
  Errors are raised (and reported per provider), never returned as [].
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .. import config

CACHE_DIR = config.STATE_DIR / "meta-cache"
DEFAULT_TTL = 7 * 24 * 3600  # a week: bios do not change hourly
UA = "HermesMediaBot/1.0 (metadata-search)"

_lock = threading.Lock()
_last_call: Dict[str, float] = {}


class ProviderError(RuntimeError):
    """A provider call failed; distinct from 'no results'."""


def _cache_path(key: str) -> Path:
    safe = urllib.parse.quote(key, safe="")[:180]
    return CACHE_DIR / f"{safe}.json"


def cached(key: str, producer: Callable[[], Any], ttl: int = DEFAULT_TTL) -> Any:
    """Return a cached value or call `producer`. Errors are not cached."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(key)
    try:
        if path.exists():
            blob = json.loads(path.read_text())
            if time.time() - blob.get("_at", 0) <= ttl:
                return blob.get("value")
    except (json.JSONDecodeError, OSError):
        pass
    value = producer()
    try:
        _atomic_write(path, {"_at": int(time.time()), "value": value})
    except OSError:
        pass
    return value


def _atomic_write(path: Path, blob: Dict[str, Any]) -> None:
    """Write via temp file + os.replace: an interrupted process must never leave
    a half-written JSON entry that reads back as corrupt."""
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(blob, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def prune_cache(max_entries: int = 4000, max_age: int = DEFAULT_TTL * 4) -> Dict[str, int]:
    """Evict stale cache entries. The cache grows without bound (README weak
    spot); call this after large sweeps, never inside hot lookups."""
    removed, kept = 0, 0
    now = time.time()
    try:
        # Stale temp files from interrupted writes never match *.json: sweep them.
        for tmp in CACHE_DIR.glob(".tmp-*"):
            try:
                if now - tmp.stat().st_mtime > 3600:
                    tmp.unlink()
                    removed += 1
            except OSError:
                pass
        files = sorted(CACHE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return {"removed": 0, "kept": 0}
    for path in files:
        try:
            blob = json.loads(path.read_text())
            age = now - blob.get("_at", 0)
        except (json.JSONDecodeError, OSError, ValueError):
            age = max_age + 1  # unreadable: evict
        if age > max_age or (len(files) - removed) > max_entries:
            try:
                path.unlink()
                removed += 1
            except OSError:
                kept += 1
        else:
            kept += 1
    return {"removed": removed, "kept": kept}


def throttle(provider: str, min_interval: float) -> None:
    """Block until `min_interval` has passed since this provider's last call."""
    with _lock:
        last = _last_call.get(provider, 0.0)
        wait = min_interval - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        _last_call[provider] = time.time()


def get_json(
    url: str,
    provider: str = "http",
    min_interval: float = 0.3,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 25,
) -> Any:
    """Rate-limited JSON GET with a clear error type."""
    throttle(provider, min_interval)
    hdrs = dict(headers or {}, **{"User-Agent": UA})
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"{provider}: HTTP {exc.code} for {url[:120]}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(
            f"{provider}: {type(exc).__name__} {str(exc)[:120]}"
        ) from exc
