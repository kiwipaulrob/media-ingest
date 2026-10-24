#!/usr/bin/env python3
"""A/V sync audit for the music libraries. Two tiers, answering different questions.

    --fast   (1-3 s/file)  Does the VIDEO stream still produce a frame in the last 10 s of
                           the container? Catches TRUNCATED picture — a file that plays for
                           twenty minutes then freezes while the audio runs on. Does not
                           measure drift.
    --full   (~25 s/file)  Compares the exact last-packet timestamp of video and audio.
                           Catches DRIFT — audio running longer than the picture (the
                           +920 ms Sabbath case), which is invisible without reading packets.

Cost here is CIFS I/O, not CPU, so --parallel is what makes a whole-library run practical:
--fast -P 6 covers ~800 files in minutes; --full -P 6 in a few hours.

    python3 scripts/sync_audit.py --fast --days 400 --parallel 6
    python3 scripts/sync_audit.py --report
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path

NODE = "root@192.168.214.131"
LIBS = ("/media/videos/musicdocos", "/media/videos/musicdocos-series")
SCRATCH = Path("/root/.hermes/profiles/media-maintainer/cache/scratch")

FAST_WORKER = r'''#!/bin/sh
export OUT=/media/sync-audit-fast.tsv
f="$1"
v=$(ffmpeg -v error -stats -sseof -10 -i "$f" -map 0:v:0 -frames:v 1 -f null - 2>&1 \
    | tr '\r' '\n' | grep -o 'frame=[ ]*[0-9]*' | tail -1 | grep -o '[0-9]*$')
dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null)
printf '%s\t%s\t%s\n' "$f" "${v:-?}" "${dur:-0}" >> "$OUT"
'''

FULL_WORKER = r'''#!/bin/sh
export OUT=/media/sync-audit-full.tsv
f="$1"
v=$(ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,duration_time \
    -of csv=p=0 "$f" 2>/dev/null | tail -1)
a=$(ffprobe -v error -select_streams a:0 -show_entries packet=pts_time,duration_time \
    -of csv=p=0 "$f" 2>/dev/null | tail -1)
printf '%s\t%s\t%s\n' "$f" "$v" "$a" >> "$OUT"
'''


def run(inner, timeout=600):
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", NODE,
         f"docker exec tdarr-node sh -c {shlex.quote(inner)}"],
        capture_output=True, text=True, timeout=timeout)


def push(name: str, content: str) -> None:
    local = SCRATCH / name
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(content)
    remote = f"/tmp/{name}"
    subprocess.run(["scp", "-q", str(local), f"root@192.168.214.131:{remote}"], check=False)
    subprocess.run(["ssh", "-o", "ConnectTimeout=12", NODE,
                    f"docker cp {remote} tdarr-node:{remote}"],
                   capture_output=True, timeout=120)


def launch(mode: str, days: int, parallel: int) -> int:
    worker = FAST_WORKER if mode == "fast" else FULL_WORKER
    out = f"/media/sync-audit-{mode}.tsv"
    push(f"sync_{mode}_worker.sh", worker)
    find = " ".join(f"'{lib}'" for lib in LIBS)
    runner = f'''#!/bin/sh
: > {out}
find {find} -type f \\( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.avi' \\) \
     -mtime -{days} -print0 \
  | xargs -0 -P {parallel} -I{{}} sh /tmp/sync_{mode}_worker.sh '{{}}'
echo "DONE $(wc -l < {out})" >> {out}
'''
    push(f"sync_{mode}_run.sh", runner)
    # detached: a full audit runs for hours and must outlive this call
    res = run(f"nohup sh /tmp/sync_{mode}_run.sh > /tmp/sync_{mode}.log 2>&1 & echo started")
    print(f"{mode} audit launched (parallel={parallel}, days={days}) -> {out}")
    print((res.stdout or res.stderr).strip()[:120])
    return 0


def report() -> int:
    shown = False
    for mode in ("full", "fast"):
        raw = run(f"cat /media/sync-audit-{mode}.tsv 2>/dev/null").stdout or ""
        rows = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("DONE")]
        if not rows:
            continue
        shown = True
        print(f"=== {mode}: {len(rows)} files ===")
        if mode == "full":
            bad = []
            for line in rows:
                p = line.split("\t")
                if len(p) < 3:
                    continue
                def end(x):
                    try:
                        pts, dur = x.split(",")[:2]
                        return float(pts) + float(dur or 0)
                    except Exception:
                        return None
                ve, ae = end(p[1]), end(p[2])
                if ve is None or ae is None:
                    continue
                d = ae - ve
                if abs(d) >= 0.75:
                    bad.append((p[0], d))
            print(f"drift flagged (>=750 ms): {len(bad)}")
            for f, d in sorted(bad, key=lambda x: -abs(x[1])):
                print(f"  {d:+9.1f}s  {f.split('/')[-1][:62]}")
        else:
            bad = [ln.split("\t") for ln in rows if ln.split("\t")[1:2] == ["0"]]
            print(f"truncated (no video at tail): {len(bad)}")
            for p in bad:
                print(f"  {p[0].split('/')[-1][:62]}  dur={p[2] if len(p) > 2 else '?'}")
    if not shown:
        print("no audit results yet")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--days", type=int, default=3650)
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        return report()
    if args.fast == args.full:
        ap.error("choose exactly one of --fast / --full (or --report)")
    return launch("fast" if args.fast else "full", args.days, args.parallel)


if __name__ == "__main__":
    raise SystemExit(main())
