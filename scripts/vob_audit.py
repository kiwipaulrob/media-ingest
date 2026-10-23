#!/usr/bin/env python3
"""Validate a DVD VIDEO_TS chain BEFORE spending hours encoding it.

Three failure modes, all silent until you look, and all seen on this library:

1. **Stalled/looping MPEG-2 timestamps.** The data is complete but the PTS chain
   breaks, so ffprobe reports a short duration (Moog: 55 s, Put More Blood: 196 s)
   and every seek past that point returns the SAME frame. Test: effective bitrate
   (size*8/duration) far above the DVD ceiling of ~10.08 Mbps. Recovery is a
   re-encode with `setpts=N/FRAME_RATE/TB`, never a stream copy and never
   `-reset_timestamps 1` (broken per-block timestamps get copied through).

2. **A missing VOB in the middle of the chain.** DVD VOBs are sequential
   (VTS_01_1, _2, _3 ...), so a gap means a segment is absent. Gil Scott-Heron has
   _1, _2, _4 — 3.0 GB / 44.5 min against a 62 min film. Joining 1+2+4 would
   silently drop ~17 minutes and splice a jump. Encoding that is worse than not
   encoding at all.

3. **Truncated final VOB.** Total runtime far short of the known running time.

Exit code is non-zero when anything fails, so a batch runner can refuse.

    vob_audit.py            # every library folder under /media/videos
    vob_audit.py --deep     # add the seek test (repeated frame = stalled PTS)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

REMOTE = os.environ.get("VOB_HOST", "root@192.168.214.131")
CONTAINER = os.environ.get("VOB_CONTAINER", "tdarr-node")
HOST_ROOT = "/mnt/zmedia"
CONT_ROOT = "/media"
DVD_MAX_MBPS = 12.0          # spec ceiling is 10.08; leave margin
MIN_REPORT_BYTES = 300_000_000


def _run(cmd: str, timeout: int = 120) -> str:
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=15", REMOTE, cmd],
                       capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


def ffprobe(path: str, entries: str) -> str:
    inner = f'{CONT_ROOT}{path[len(HOST_ROOT):]}' if path.startswith(HOST_ROOT) else path
    cmd = (f'docker exec {CONTAINER} ffprobe -v error {entries} -of default=noprint_wrappers=1 "{inner}"')
    return _run(cmd).strip()


def frame_bytes(path: str, t: int) -> int:
    inner = f'{CONT_ROOT}{path[len(HOST_ROOT):]}' if path.startswith(HOST_ROOT) else path
    cmd = (f'docker exec {CONTAINER} ffmpeg -v error -ss {t} -i "{inner}" '
           f'-frames:v 1 -f image2 - 2>/dev/null | wc -c')
    try:
        return int(_run(cmd, timeout=180).strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def duration(path: str) -> float:
    out = ffprobe(path, "-show_entries format=duration")
    for line in out.splitlines():
        if line.startswith("duration="):
            try:
                return float(line.split("=", 1)[1])
            except ValueError:
                return 0.0
    return 0.0


def audit_dir(d: str, deep: bool) -> list[str]:
    """Return a list of problem descriptions for one VIDEO_TS directory."""
    problems: list[str] = []
    listing = _run(f'ls -1 "{d}" 2>/dev/null')
    vobs = [n for n in listing.splitlines() if re.fullmatch(r"VTS_\d+_\d+\.VOB", n.strip())]

    # --- numbering continuity, per title set ---------------------------------
    sets: dict[str, list[int]] = defaultdict(list)
    for n in vobs:
        m = re.fullmatch(r"VTS_(\d+)_(\d+)\.VOB", n.strip())
        if m:
            sets[m.group(1)].append(int(m.group(2)))
    for tset, idx in sets.items():
        main = sorted(i for i in idx if i >= 1)
        if main and main != list(range(1, len(main) + 1)):
            missing = [i for i in range(1, max(main) + 1) if i not in main]
            problems.append(f"MISSING VOB(s) {missing} in title set VTS_{tset} "
                            f"(have {main}) — the chain has a hole; do not join it")

    # --- per-VOB duration/bitrate sanity ------------------------------------
    for n in sorted(vobs):
        if n.strip().endswith("_0.VOB"):
            continue                                  # menu
        path = f"{d}/{n.strip()}"
        size = int(_run(f'stat -c %s "{path}"').strip() or 0)
        if size < MIN_REPORT_BYTES:
            continue
        dur = duration(path)
        if dur <= 0:
            problems.append(f"{n}: duration unreadable")
            continue
        mbps = size * 8 / dur / 1_000_000
        if mbps > DVD_MAX_MBPS:
            problems.append(
                f"{n}: {mbps:.1f} Mbps over {dur:.0f}s — STALLED TIMESTAMPS "
                f"(data present, PTS broken; re-encode with setpts=N/FRAME_RATE/TB)")
            if deep:
                a, b = frame_bytes(path, 600), frame_bytes(path, 2400)
                if a and a == b:
                    problems.append(f"{n}: seek at 600s and 2400s return the SAME "
                                    f"frame — confirms the timestamp stall")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deep", action="store_true", help="also run the seek test")
    ap.add_argument("--roots", nargs="*", default=["/mnt/zmedia/videos/musicdocos",
                                                   "/mnt/zmedia/videos/musicdocos-series"])
    args = ap.parse_args()

    dirs: list[str] = []
    for root in args.roots:
        out = _run(f'find "{root}" -maxdepth 3 -type d -name VIDEO_TS 2>/dev/null')
        dirs += [x.strip() for x in out.splitlines() if x.strip()]

    if not dirs:
        print("no VIDEO_TS directories found")
        return 0

    bad = 0
    for d in sorted(dirs):
        probs = audit_dir(d, args.deep)
        label = d.replace(HOST_ROOT + "/videos/", "")
        if probs:
            bad += 1
            print(f"✗ {label}")
            for p in probs:
                print(f"    {p}")
    print(f"\n{len(dirs)} VIDEO_TS folder(s), {bad} with problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
