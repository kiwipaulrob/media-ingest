"""Scan a folder for series-shaped filenames and propose series moves.

Gap this closes: the suite had no series discovery — multi-episode sets sat
in Documentaries until a human noticed the naming pattern (Vasari, Slumming
It, Riviera all surfaced via a manual 100-row gate, not via any sweep).
Read-only: prints proposal JSON, moves nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, ".")

from media_lib import matcher, tmdb

VIDEO_EXT = (".mkv", ".mp4", ".avi")
EP_PATTERNS = [
    re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})"),          # S01E02
    re.compile(r"(\d{1,2})[xX](\d{1,2})"),               # 1x02
    re.compile(r"[Ee]p(?:isode)?[\s._-]*(\d{1,2})", re.I),  # ep02
    re.compile(r"[Pp]art[\s._-]*(\d{1,2})"),            # part 2
    re.compile(r"(\d{1,2})\s*of\s*(\d{1,2})", re.I),    # 2 of 5
    re.compile(r"[Pp]t[\s._]*(\d{1,2})\b"),             # pt2
]


def episode_no(stem: str):
    """(season, episode) or None. Season defaults to 1 for bare part numbers."""
    m = re.search(r"[Ss](\d{1,2})[Ee](\d{1,2})", stem)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{1,2})[xX](\d{1,2})", stem)
    if m:
        return int(m.group(1)), int(m.group(2))
    # Bare "e01" (dotted releases) and "ep05"/"episode 2", but never bare
    # "e 8" — that false-positives on titles like "Grade 8". A bare "e"
    # allows only ./_/nothing as separator; the p-form allows spaces too.
    m = re.search(r"\be(?:p(?:isode)?[\s._-]*|[._]*)(\d{1,2})\b", stem, re.I)
    if m:
        return 1, int(m.group(1))
    for pat in EP_PATTERNS[2:]:
        m = pat.search(stem)
        if m:
            groups = m.groups()
            if len(groups) == 2:
                return int(groups[0]), int(groups[1])
            return 1, int(groups[0])
    return None


def strip_episode_markers(name: str) -> str:
    """Remove SxxEyy/part/episode markers to get a groupable series title."""
    base = matcher.clean_release(name)
    base = re.sub(r"[Ss]\d{1,2}[Ee]\d{1,2}", " ", base)
    base = re.sub(r"\b\d{1,2}[xX]\d{1,2}\b", " ", base)
    base = re.sub(r"(?i)\bep(?:isode)?[\s._-]*\d{1,2}\b", " ", base)
    base = re.sub(r"(?i)\bpart[\s._-]*\d{1,2}\b", " ", base)
    base = re.sub(r"(?i)\bpt[\s._]*\d{1,2}\b", " ", base)
    base = re.sub(r"(?i)\b\d{1,2}\s*of\s*\d{1,2}\b", " ", base)
    base = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", base)
    base = re.sub(r"(?i)\b(wna|hannibal|afg|mvgroup|aac|x264|xvid|hdtv|pdtv|web|dl|bluray|dvdrip|divx)\b", " ", base)
    return re.sub(r"\s+", " ", base).strip(" -").lower()


def scan(root: str) -> list:
    """Group series-shaped files by title. Pure filesystem walk."""
    paths = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.lower().endswith(VIDEO_EXT):
                paths.append(os.path.join(dirpath, fn))
    return group(paths)


def group(paths: list) -> list:
    """Group series-shaped paths by title. Pure — feeds from any listing."""
    groups: dict = {}
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        ep = episode_no(stem)
        if not ep:
            continue
        title = strip_episode_markers(stem)
        if len(title) < 3:
            continue
        groups.setdefault(title, []).append(
            {"file": path, "season": ep[0], "episode": ep[1]}
        )
    proposals = []
    for title, eps in sorted(groups.items()):
        if len(eps) < 2:
            continue
        try:
            hit = tmdb.search(title, None, "tv")
        except Exception:  # noqa: BLE001 - gate must not die on network
            hit = None
        proposals.append(
            {
                "title": title,
                "files": len(eps),
                "episodes": sorted((e["season"], e["episode"]) for e in eps),
                "tmdb": (
                    {"id": hit["id"], "name": hit["title"], "year": hit["year"]}
                    if hit
                    else None
                ),
            }
        )
    return proposals


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Propose series moves (read-only).")
    ap.add_argument("--root", default=None, help="folder to walk (local only)")
    ap.add_argument("--from-stdin", action="store_true",
                    help="read one file path per line from stdin (remote listings)")
    ap.add_argument("--min-files", type=int, default=2)
    a = ap.parse_args(argv)
    if a.from_stdin:
        paths = [ln.strip() for ln in sys.stdin if ln.strip()]
        out = [p for p in group(paths) if p["files"] >= a.min_files]
    else:
        if not a.root:
            ap.error("--root or --from-stdin is required")
        out = [p for p in scan(a.root) if p["files"] >= a.min_files]
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
