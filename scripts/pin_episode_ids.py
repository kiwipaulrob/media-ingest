"""Pin TMDB episode ids onto library episodes, matched BY NAME.

Read-only by default; `--apply` writes ProviderIds.Tmdb (and fills an empty
Overview when --with-overview). NEVER matches by episode number — that is
the defect this exists to avoid (TMDB numbering does not follow a
hand-built library's layout).

Reports counts only: a 1,000-episode strand prints a summary, not 1,000
lines, so a sweep costs a few hundred tokens instead of tens of thousands.

    python3 scripts/pin_episode_ids.py --library "Music Docos Series" \
        --series-frag "Jools" [--apply] [--with-overview]
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, ".")

from media_lib import jellyfin, tmdb  # noqa: E402

#: Words that identify nothing in an episode title.
STOP = {
    "the", "and", "for", "with", "from", "that", "this", "his", "her", "their",
    "part", "episode", "series", "bbc", "live", "new", "old", "one", "two",
}


def tokens(text: str) -> set:
    """Distinctive lowercase words of a title. Pure."""
    words = re.findall(r"[a-z]{3,}", str(text or "").lower())
    return {w for w in words if w not in STOP}


def best_match(name: str, index: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """TMDB episode whose title shares the most distinctive words. Pure.

    Requires at least one shared word, and prefers a unique winner: a tie
    between different episodes is ambiguous and returns None (never guess).
    """
    want = tokens(name)
    if not want:
        return None
    scored = []
    for key, ep in index.items():
        have = set(key.split())
        overlap = len(want & have)
        if overlap:
            scored.append((overlap, ep))
    if not scored:
        return None
    scored.sort(key=lambda t: -t[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        # tie on score: only accept if both point at the same episode
        if scored[0][1].get("id") != scored[1][1].get("id"):
            return None
    return scored[0][1]


def season_index(series_tmdb: int, max_season: int = 40) -> Dict[str, Dict[str, Any]]:
    """{normalised episode title: episode} across all seasons. Never raises."""
    index: Dict[str, Dict[str, Any]] = {}
    for season in range(0, max_season + 1):
        try:
            data = tmdb._get(f"/tv/{series_tmdb}/season/{season}", {})
        except Exception:  # noqa: BLE001 - a missing season 404s
            continue
        for ep in (data or {}).get("episodes") or []:
            name = ep.get("name") or ""
            key = " ".join(sorted(tokens(name)))
            if key:
                index.setdefault(key, ep)
    return index


def plan(rows: List[Dict[str, Any]], index: Dict[str, Dict[str, Any]]) -> Dict[str, List]:
    """Split library episodes into matched / unmatched / ambiguous. Pure."""
    matched, unmatched, ambiguous = [], [], []
    for r in rows:
        if not isinstance(r, dict):
            continue
        ep = best_match(r.get("Name") or "", index)
        if ep:
            matched.append((r, ep))
        elif tokens(r.get("Name") or ""):
            unmatched.append(r)
        else:
            ambiguous.append(r)
    return {"matched": matched, "unmatched": unmatched, "ambiguous": ambiguous}


def series_targets(uid: str, lib_id: str) -> List[Dict[str, Any]]:
    """Series rows in a library that carry a TMDB id. Read-only."""
    rows = list(jellyfin.list_items(
        uid, parent_id=lib_id, include_types="Series", fields="ProviderIds,Name") or [])
    return [r for r in rows
            if isinstance(r, dict) and (r.get("ProviderIds") or {}).get("Tmdb")]


def run_all(a) -> int:
    """Sweep every TMDB-identified series; one summary line each."""
    uid = jellyfin.user_id()
    folders: Any = jellyfin.get_json("/Library/VirtualFolders") or []
    libs = {f.get("Name"): f.get("ItemId") for f in folders if isinstance(f, dict)}
    if a.library not in libs:
        print(f"unknown library {a.library!r}")
        return 2
    lib_id = libs[a.library] or ""
    total_m = total_u = total_p = 0
    for s in series_targets(uid, lib_id):
        tid = int((s.get("ProviderIds") or {})["Tmdb"])
        episodes = list(jellyfin.list_items(
            uid, parent_id=s["Id"], recursive=True, include_types="Episode",
            fields="ProviderIds,Overview,Name") or [])
        needing = [e for e in episodes
                   if isinstance(e, dict)
                   and not (e.get("ProviderIds") or {}).get("Tmdb")
                   and not (e.get("ProviderIds") or {}).get("Imdb")]
        if not needing:
            continue
        index = season_index(tid)
        p = plan(needing, index)
        m, u = len(p["matched"]), len(p["unmatched"]) + len(p["ambiguous"])
        total_m += m
        total_u += u
        print(f"   {str(s.get('Name'))[:38]:<40} need={len(needing):<4} "
              f"match={m:<4} miss={u}")
        if a.apply and p["matched"]:
            total_p += apply_pins(uid, p["matched"], a.with_overview)
    print(f"== {a.library}: matchable {total_m}, unmatchable {total_u}"
          + (f", written {total_p}" if a.apply else " (dry run)"))
    return 0


def apply_pins(uid: str, matched: List, with_overview: bool) -> int:
    """Pin matched episodes. Returns rows written."""
    written = 0
    for r, ep in matched:
        dto = jellyfin.get_json(f"/Users/{uid}/Items/{r['Id']}") or {}
        prov = dict(dto.get("ProviderIds") or {})
        changed = False
        if not prov.get("Tmdb") and ep.get("id"):
            prov["Tmdb"] = str(ep["id"])
            dto["ProviderIds"] = prov
            changed = True
        if (with_overview and len(dto.get("Overview") or "") < 50
                and len(ep.get("overview") or "") >= 50):
            dto["Overview"] = ep["overview"]
            changed = True
        if not changed:
            continue
        dto.pop("Genres", None)
        try:
            jellyfin.post_item(r["Id"], dto)
            written += 1
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the run
            print(f"   WRITE-FAIL {str(r.get('Name'))[:30]}: {type(exc).__name__}")
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pin TMDB episode ids by name.")
    ap.add_argument("--library", required=True)
    ap.add_argument("--series-frag", default=None,
                    help="one series (substring match); omit with --all")
    ap.add_argument("--all", action="store_true", help="every TMDB-id series in the library")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--with-overview", action="store_true")
    ap.add_argument("--show-unmatched", type=int, default=5)
    a = ap.parse_args(argv)
    if a.all:
        return run_all(a)
    if not a.series_frag:
        ap.error("give --series-frag or --all")

    uid = jellyfin.user_id()
    folders: Any = jellyfin.get_json("/Library/VirtualFolders") or []
    libs = {f.get("Name"): f.get("ItemId") for f in folders if isinstance(f, dict)}
    if a.library not in libs:
        print(f"unknown library {a.library!r}")
        return 2
    series_rows = list(jellyfin.list_items(
        uid, parent_id=libs[a.library], include_types="Series",
        fields="ProviderIds,Name") or [])
    target = next(
        (s for s in series_rows
         if isinstance(s, dict) and a.series_frag.lower() in str(s.get("Name")).lower()),
        None)
    if not target:
        print(f"no series matching {a.series_frag!r}")
        return 2
    tid = (target.get("ProviderIds") or {}).get("Tmdb")
    if not tid:
        print(f"series {target.get('Name')!r} has no TMDB id — identify it first")
        return 2

    index = season_index(int(tid))
    episodes = list(jellyfin.list_items(
        uid, parent_id=target["Id"], recursive=True, include_types="Episode",
        fields="ProviderIds,Overview,Name") or [])
    p = plan(episodes, index)
    print(f"== {target.get('Name')} (tmdb {tid}): {len(episodes)} eps, "
          f"{len(index)} TMDB titles")
    print(f"   matched    {len(p['matched'])}")
    print(f"   unmatched  {len(p['unmatched'])}")
    print(f"   no-title   {len(p['ambiguous'])}")
    if not a.apply:
        print("   (dry run — pass --apply to pin)")
        return 0

    pinned = ov = 0
    for r, ep in p["matched"]:
        dto = jellyfin.get_json(f"/Users/{uid}/Items/{r['Id']}") or {}
        prov = dict(dto.get("ProviderIds") or {})
        changed = False
        if not prov.get("Tmdb") and ep.get("id"):
            prov["Tmdb"] = str(ep["id"])
            dto["ProviderIds"] = prov
            changed = True
        if (a.with_overview and len(dto.get("Overview") or "") < 50
                and len(ep.get("overview") or "") >= 50):
            dto["Overview"] = ep["overview"]
            changed = True
            ov += 1
        if changed:
            dto.pop("Genres", None)
            try:
                jellyfin.post_item(r["Id"], dto)
                pinned += 1
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the run
                print(f"   WRITE-FAIL {str(r.get('Name'))[:30]}: {type(exc).__name__}")
    print(f"   written {pinned} (overviews filled {ov})")
    for r in p["unmatched"][: a.show_unmatched]:
        print(f"   unmatched: {str(r.get('Name'))[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
