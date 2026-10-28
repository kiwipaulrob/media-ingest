"""Episode-level metadata audit (counts first, detail on request).

Read-only. Aggregates by library and by series so a 2,000-row sweep prints
a summary instead of 37k tokens of row dumps; the full detail goes to a
JSON file for drilling in later.

    python3 scripts/audit_episodes.py --library "Docos Series" [--detail TOP]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, ".")

from media_lib import jellyfin  # noqa: E402


def episode_flags(row: Dict[str, Any]) -> List[str]:
    """Missing-field flags for one episode/movie row. Pure.

    A row with no Path is a VIRTUAL episode: Jellyfin materialises an empty
    slot for every TMDB episode a series has, most of which this library
    never expected to hold. Counting those as gaps buried the real ones
    (550 of 876 rows in Music Docos Series were virtual). They are returned
    as ['virtual'] and are not a maintenance signal.
    """
    if not row.get("Path"):
        return ["virtual"]
    prov = row.get("ProviderIds") or {}
    ov = row.get("Overview") or ""
    tags = row.get("ImageTags") or {}
    out = []
    if not prov.get("Tmdb") and not prov.get("Imdb"):
        out.append("no-ids")
    if len(ov) < 50:
        out.append("no-ov")
    if not tags.get("Primary"):
        out.append("no-still")
    return out


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts by flag combination + per-series tallies. Pure."""
    combos: Dict[str, int] = {}
    per_series: Dict[str, Dict[str, int]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        f = episode_flags(r)
        key = "+".join(f) if f else "clean"
        combos[key] = combos.get(key, 0) + 1
        series = str(r.get("SeriesName") or "(film)")
        bucket = per_series.setdefault(series, {})
        for flag in f:
            bucket[flag] = bucket.get(flag, 0) + 1
    worst = sorted(
        ((s, b.get("no-ids", 0), b.get("no-ov", 0), sum(b.values()))
         for s, b in per_series.items()),
        key=lambda t: (-t[1], -t[2], t[0]),
    )
    return {"total": len(rows), "combos": combos, "worst": worst[:40]}


def audit(library: str) -> List[Dict[str, Any]]:
    """All episode/movie rows for a library. Read-only, never raises.

    One query PER TYPE: a comma-joined include_types string silently returned
    extra item kinds (seasons/folders), which inflated the first sweep's
    counts (a 109-episode strand reported as 1,011 rows).
    """
    uid = jellyfin.user_id()
    folders: Any = jellyfin.get_json("/Library/VirtualFolders") or []
    libs = {f.get("Name"): f.get("ItemId") for f in folders if isinstance(f, dict)}
    if library not in libs:
        return []
    rows: List[Dict[str, Any]] = []
    for kind in ("Episode", "Movie", "Video"):
        try:
            rows.extend(jellyfin.list_items(
                uid, parent_id=libs[library], recursive=True, include_types=kind,
                fields="ProviderIds,Overview,ImageTags,SeriesName,Name,Path"))
        except Exception:  # noqa: BLE001 - a missing type is not a failure
            continue
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Episode metadata audit (read-only).")
    ap.add_argument("--library", required=True)
    ap.add_argument("--detail", type=int, default=0,
                    help="print the N worst series rows (default 0 = counts only)")
    ap.add_argument("--out", default=None, help="JSON detail path")
    a = ap.parse_args(argv)
    rows = audit(a.library)
    s = summarise(rows)
    real = s["total"] - s["combos"].get("virtual", 0)
    print(f"== {a.library}: {s['total']} rows ({real} with media, "
          f"{s['combos'].get('virtual', 0)} virtual slots ignored)")
    for k, v in sorted(s["combos"].items(), key=lambda kv: -kv[1]):
        print(f"   {k:<26} {v}")
    if a.detail:
        print("   worst series (no-ids, no-ov):")
        for name, noid, noov, tot in s["worst"][: a.detail]:
            if not (noid or noov):
                continue
            print(f"     {name[:46]:<48} ids={noid:<4} ov={noov:<4} of {tot}")
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"summary": s, "rows": rows}, indent=1))
        print(f"   detail -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
