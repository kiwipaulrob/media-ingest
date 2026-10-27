"""Pre-placement season slot auditing (Review-P3.3).

The Def Leppard / Sex Pistols collision: TMDB numbered Sex Pistols S04E02
while the disk held it at S04E01, so placing Def Leppard at TMDB's S04E01
put two files on one slot and hid one from the UI. The disk is the truth;
TMDB numbering is advisory. `place_new` calls `refuse_collision()` before
copying a single byte.
"""

from __future__ import annotations

import re
import shlex
from typing import List, Optional

_SLOT = re.compile(r"[Ss]0*(\d{1,2})[EeXx]0*(\d{1,2})")


def slot_of(filename: str) -> Optional[str]:
    """Normalised 'S02E03' slot for a filename, or None. Pure."""
    n = slot_numbers(filename)
    if not n[0] and not n[1]:
        return None
    return f"S{n[0]:02d}E{n[1]:02d}"


def slot_numbers(filename: str) -> tuple:
    """(season, episode) ints from an SxxEyy token, else (0, 0). Pure."""
    m = _SLOT.search(filename or "")
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)))


def season_dir_of(dest: str) -> Optional[str]:
    """The Season folder a series-style dest lives in, or None. Pure."""
    raw = str(dest).replace("\\", "/")
    parts = [p for p in raw.split("/") if p]
    for i, p in enumerate(parts[:-1]):
        if re.search(r"[Ss]eason\s+\d+", p):
            prefix = "/" if raw.startswith("/") else ""
            return prefix + "/".join(parts[: i + 1])
    return None


def collision(listing: List[str], filename: str) -> Optional[str]:
    """Name of the occupying file when `filename`'s slot is taken, else None.

    Pure. Same slot + different stem = refuse; same file re-placed = fine.
    """
    want = slot_of(filename)
    if not want:
        return None
    leaf = filename.rsplit("/", 1)[-1]
    for other in listing:
        if other == leaf or other.rsplit("/", 1)[-1] == leaf:
            continue
        if slot_of(other) == want:
            return other
    return None


def refuse_collision(dest_disk: str) -> Optional[str]:
    """Live season-folder check. Returns the occupier or None. Never raises."""
    from . import probe  # deferred: light import for tests

    season = season_dir_of(dest_disk)
    leaf = dest_disk.rsplit("/", 1)[-1]
    if not season or not slot_of(leaf):
        return None
    try:
        out = probe.on_media(f"ls -1 {shlex.quote(season)} 2>/dev/null", timeout=60)
    except Exception:  # noqa: BLE001 - an unreadable folder is not a collision
        return None
    return collision(out.splitlines(), leaf)
