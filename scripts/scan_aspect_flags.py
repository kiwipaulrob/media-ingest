"""Flag library files whose container aspect flag contradicts their raster.

Proven case: a 720x400 XviD carried SAR 25:36 (DAR 5:4) on widescreen content
and played narrow; the pixels were right, the flag was wrong (`setsar=1`
repair). Legit anamorphic discs (720x576 -> 16:9) must NOT flag: narrow
raster + wide DAR is correct by design. Only the contradictory direction
flags.

    python3 scripts/scan_aspect_flags.py [--libs Docos "Music Docos"]
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Optional, Tuple

sys.path.insert(0, ".")


def parse_ratio(ar: str) -> Optional[float]:
    """'16:9' -> 1.778, '1.85:1' -> 1.85. None when unparseable. Pure."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$", ar or "")
    if not m or float(m.group(2)) == 0:
        return None
    return float(m.group(1)) / float(m.group(2))


def classify(ar: str, w: int, h: int) -> Tuple[str, str]:
    """(verdict, reason) for one file. Pure.

    Verdicts: 'ok' | 'suspect-narrow' | 'suspect-wide' | 'unknown'.
    Thresholds: raster-wide means w/h >= 1.7; flag-narrow means DAR < 1.5.
    """
    dar = parse_ratio(ar)
    if not dar or not w or not h:
        return "unknown", f"ar={ar} raster={w}x{h}"
    raster = w / h
    if raster >= 1.7 and dar < 1.5:
        return ("suspect-narrow",
                f"wide raster {w}x{h} ({raster:.2f}) flagged DAR {ar} — "
                f"plays squeezed; SAR repair candidate")
    if raster < 1.2 and dar >= 1.7:
        # Below anamorphic territory (720x576 = 1.25 is legit): a near-square
        # raster flagged widescreen is almost never correct.
        return ("suspect-wide",
                f"near-square raster {w}x{h} ({raster:.2f}) flagged DAR {ar}")
    return "ok", f"raster {raster:.2f} vs DAR {dar:.2f}"


def main(argv=None) -> int:
    from media_lib import jellyfin  # deferred: network only in main

    ap = argparse.ArgumentParser(description="Flag aspect-contradicted files.")
    ap.add_argument("--libs", nargs="+",
                    default=["Docos", "Music Docos", "Film Docos", "Film Noir"])
    a = ap.parse_args(argv)
    uid = jellyfin.user_id()
    from typing import Any, Dict, List

    raw_folders: Any = jellyfin.get_json("/Library/VirtualFolders") or []
    folders: List[Dict[str, Any]] = raw_folders if isinstance(raw_folders, list) else []
    lib_ids = {f.get("Name"): f.get("ItemId") for f in folders
               if isinstance(f, dict)}
    n_ok = n_unknown = 0
    suspects = []
    for lib in a.libs:
        try:
            rows = jellyfin.list_items(
                uid, parent_id=lib_ids[lib], recursive=True,
                include_types="Movie", fields="Name,Width,Height,MediaSources,Path") or []
        except Exception as exc:  # noqa: BLE001 - one library down never stops the scan
            print(f"  {lib}: unreadable ({type(exc).__name__})")
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            ar, w, h = "?", r.get("Width") or 0, r.get("Height") or 0
            for src in (r.get("MediaSources") or [])[:1]:
                for s in (src.get("MediaStreams") or []):
                    if s.get("Type") == "Video":
                        ar = str(s.get("AspectRatio") or "?")
            verdict, reason = classify(ar, w, h)
            if verdict == "ok":
                n_ok += 1
            elif verdict == "unknown":
                n_unknown += 1
            else:
                suspects.append((verdict, str(r.get("Name"))[:56],
                                 f"{w}x{h}", ar, str(r.get("Path"))[:70], reason))
    print(f"ok: {n_ok}  unknown: {n_unknown}  suspects: {len(suspects)}")
    for v, name, res, ar, path, why in sorted(suspects):
        print(f"  [{v}] {res} DAR={ar} {name}\n        {path}\n        {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
