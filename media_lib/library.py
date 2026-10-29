"""Library dump: the set of items a release can be matched against.

Built from the REST API, not the filesystem, and cached with an explicit age.
A stale dump holds item ids that stop resolving the moment anything swaps a
file, so the cache is timestamped and callers must rebuild past the threshold.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from . import config, jellyfin, matcher


def _lib_for(jf_path: str) -> Optional[str]:
    """Map a container path to its library root, longest prefix first.

    Boundary-aware: /data/movies-extra/x.mkv must NOT match /data/movies (it
    would corrupt the disk path into .../Films-extra/...). place.to_disk() was
    fixed for this exact bug; this was missed.
    """
    best: Optional[str] = None
    for prefix in config.JF_TO_DISK:
        if (jf_path == prefix or jf_path.startswith(prefix + "/")) and (
            best is None or len(prefix) > len(best)
        ):
            best = prefix
    return best


def _row(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    path = item.get("Path") or ""
    lib_path = _lib_for(path)
    if lib_path is None or not item.get("Name"):
        return None
    streams: List[Dict[str, Any]] = []
    for src in item.get("MediaSources") or []:
        streams = src.get("MediaStreams") or []
        break
    video = next((s for s in streams if s.get("Type") == "Video"), {})
    name = item["Name"]
    row = {
        "id": item.get("Id"),
        "type": item.get("Type"),
        "Name": name,
        "path": path,
        "lib": lib_path,
        "disk": config.JF_TO_DISK[lib_path] + path[len(lib_path):],
        "tokens": sorted(matcher.tokens(matcher.clean_release(name))),
        "squash": matcher.squash(matcher.clean_release(name)),
        "w": video.get("Width"),
        "h": video.get("Height"),
        "codec": video.get("Codec"),
        "nsrc": len(item.get("MediaSources") or []) or 1,
    }
    # Series identity for episodic rows (review-3): without these, plan.py
    # cannot match an episodic upgrade to its row and files it as NEW.
    if item.get("Type") == "Episode":
        row["series"] = item.get("SeriesName")
        row["season"] = item.get("ParentIndexNumber")
        row["episode"] = item.get("IndexNumber")
    return row


def build(uid: Optional[str] = None) -> Dict[str, Any]:
    """Dump every video item, grouped by library root."""
    uid = uid or jellyfin.user_id()
    # One query per type: a comma-joined IncludeItemTypes returned extra item
    # kinds on this build (lesson 35) — never trust the joined form.
    # Video stays: extras/featurettes live under it.
    items = []
    for itype in ("Movie", "Episode", "Video"):
        items.extend(jellyfin.list_items(
            uid,
            recursive=True,
            include_types=itype,
            fields="Path,MediaSources,ProductionYear,SeriesName,ParentIndexNumber,IndexNumber",
        ) or [])
    libs: Dict[str, List[Dict[str, Any]]] = {p: [] for p in config.JF_TO_DISK}
    for it in items:
        r = _row(it)
        if r:
            libs[r["lib"]].append(r)
    return {
        "_built": int(time.time()),
        "libs": libs,
        "count": sum(len(v) for v in libs.values()),
    }


def load(
    max_age: Optional[int] = None, uid: Optional[str] = None, path=None
) -> Dict[str, Any]:
    """Cached dump, rebuilt when absent or older than max_age."""
    p = path or config.LIB_CACHE
    max_age = config.LIB_CACHE_STALE_S if max_age is None else max_age
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if time.time() - data.get("_built", 0) <= max_age:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    data = build(uid)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
    except OSError:
        pass
    return data
