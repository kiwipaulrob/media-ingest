"""Safety primitives: the lock, the ledger, and the human check list.

These three exist because of specific failures, not for tidiness:

* LOCK — "never run two intakes at once" used to be a sentence in a skill doc.
  Two concurrent runs can both decide a torrent is unprocessed and both swap
  files. The lock makes that structurally impossible.
* LEDGER — qBittorrent returns 40-char hashes while the ledger stored 12-char
  prefixes, so `full_hash not in known` never matched and EVERY previously
  ingested torrent was re-processed on every run. Prefixes are normalised here
  on the way in AND on the way out.
* CHECK LIST — must be byte-stable, because a byte-stable monitor is what keeps
  a cron job from waking the agent when nothing changed.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List

from . import config

HASH_PREFIX_LEN = 12


class LockHeld(RuntimeError):
    """Raised when another ingest run already holds the lock."""


def hash_prefix(h: str) -> str:
    """Normalise any hash form to the stored 12-char prefix."""
    return str(h or '').strip().lower()[:HASH_PREFIX_LEN]


def load_ledger(path: Path | str | None = None) -> Dict[str, Any]:
    """Read the ledger, tolerating a missing or empty file."""
    p = Path(path or config.LEDGER)
    if not p.exists():
        return {}
    try:
        with p.open() as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def ledger_contains(ledger: Dict[str, Any], full_hash: str) -> bool:
    """True when this torrent is already recorded, in EITHER hash form."""
    h = str(full_hash or '').strip().lower()
    if not h:
        return False
    return h in ledger or hash_prefix(h) in ledger


def save_ledger(ledger: Dict[str, Any], path: Path | str | None = None) -> None:
    """Atomically replace the ledger (temp file + os.replace)."""
    p = Path(path or config.LEDGER)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix='.ledger-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as fh:
            json.dump(ledger, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def record(path: Path | str | None = None, **fields: Any) -> Dict[str, Any]:
    """Append one ledger entry keyed on the 12-char prefix.

    Call this the moment an item succeeds — never once at the end of a run. A
    killed run that loses the ledger forgets every replacement it completed.
    """
    ledger = load_ledger(path)
    h = fields.get('hash') or fields.get('full_hash') or ''
    key = hash_prefix(h)
    if not key:
        raise ValueError('record() needs a hash')
    entry = {k: v for k, v in fields.items() if k not in ('hash', 'full_hash')}
    entry.setdefault('full_hash', str(h).lower())
    ledger[key] = entry
    save_ledger(ledger, path)
    return entry


@contextlib.contextmanager
def acquire(path: Path | str | None = None) -> Iterator[None]:
    """Hold the ingest lock; raise LockHeld if another run has it.

    Non-blocking on purpose: a queued second run would eventually act on a plan
    built before the first run changed the library underneath it.
    """
    p = Path(path or config.LOCK)
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = p.open('a+')
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LockHeld(f'another ingest run holds {p}') from exc
        fh.seek(0)
        fh.truncate()
        fh.write(f'pid={os.getpid()}\n')
        fh.flush()
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def write_check_list(rows: List[Dict[str, Any]], path: Path | str | None = None) -> Path:
    """Write the rows a human must judge. Byte-stable for monitor gating."""
    p = Path(path or config.CHECK_LIST)
    p.parent.mkdir(parents=True, exist_ok=True)
    clean: List[Dict[str, Any]] = []
    for r in rows or []:
        clean.append({k: r[k] for k in sorted(r) if k != '_lib'})
    # Stable ordering independent of dict iteration: by recommend then title.
    clean.sort(key=lambda r: (str(r.get('recommend') or ''), str(r.get('title') or '')))
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix='.check-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as fh:
            json.dump(clean, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return p
