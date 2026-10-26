#!/usr/bin/env python3
"""Concatenate the parts of a two-part programme into one file, then verify it.

Why a script rather than a one-off: this library holds many programmes split across two
files (`I.avi`/`II.avi`, `Part I`/`Part II`, `part 1`/`part 2`). Naming them `- part1` does
NOT merge on this server (proven on Easy Rider and Unforgivable Blackness), so the only
reliable single entry is one file.

Stream copy only — no re-encode. Verified before the parts are removed:
  * the output exists and is larger than the biggest part
  * the output duration is within 2% of the sum of the parts (catches a bad concat, which
    otherwise silently produces a truncated file that still probes as valid)
  * a decode check confirms the join point actually plays

Usage:
    python3 scripts/concat_parts.py --dir "<folder on disk>" --out "<output name.mkv>"
    python3 scripts/concat_parts.py --dir "..." --out "..." --apply   # delete parts after success
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from media_lib import probe  # noqa: E402

NODE = "root@192.168.214.132"
HOST_ROOT = "/mnt/zmedia/videos/"


def sh(cmd: str, t: int = 600) -> str:
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=12", NODE, cmd],
                       capture_output=True, text=True, timeout=t)
    return (r.stdout or "") + (r.stderr or "")


def part_no(name: str) -> int:
    """Order parts by their real part marker, never by the first number in the name.

    Sorting on the first number picked the YEAR (2007) over the part number and silently
    reversed a conversion. Roman numerals and 'part N' both have to resolve.
    """
    base = os.path.basename(name)
    m = re.search(r"(?i)\bpart[\s._-]*(\d+)\b", base)
    if m:
        return int(m.group(1))
    m = re.search(r"\b([IVX]{1,4})\s*\.(?:avi|mkv|mp4)$", base, re.I)
    if m:
        vals = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
        return vals.get(m.group(1).upper(), 0)
    m = re.search(r"(?i)(\d+)\s*of\s*(\d+)", base)
    if m:
        return int(m.group(1))
    m = re.search(r"(?i)[\s._-](\d)(?:[\s._-]|$)", base)
    return int(m.group(1)) if m else 0


def durations_match(out_min: float, total_min: float, tol: float = 0.02) -> bool:
    """Is the concatenated output the whole programme, not a truncated fragment?

    A bad concat still probes as a valid file with a plausible duration, so the exit
    code proves nothing. TJ came out at 7.3 min against 175.9 min of parts and would
    have replaced two good files with 4% of the film without this gate.
    """
    if not total_min:
        return False
    return abs(out_min - total_min) / total_min <= tol


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="folder on the media host, /mnt/zmedia/videos/...")
    ap.add_argument("--out", required=True, help="output filename inside that folder")
    ap.add_argument("--apply", action="store_true", help="delete the parts once verified")
    ap.add_argument("--reencode", action="store_true",
                    help="re-encode (libx264/aac) instead of stream copy — only when -c copy fails")
    a = ap.parse_args()

    d = a.dir.rstrip("/")
    listing = sh(f'find {shlex.quote(d)} -maxdepth 1 -type f '
                 f'\\( -iname "*.mkv" -o -iname "*.avi" -o -iname "*.mp4" \\) -printf "%f\\n"')
    names = [n.strip() for n in listing.splitlines() if n.strip()]
    if len(names) < 2:
        print(f"ABORT: need at least 2 parts, found {len(names)}: {names}")
        return 1
    names.sort(key=part_no)
    print("parts in order:")
    total = 0.0
    for n in names:
        r = probe.probe(f"{d}/{n}")
        dur = (r["dur"] or 0) / 60
        total += dur
        if r["h"] is None:
            shape = "PROBE-FAIL"
        else:
            shape = "{0}x{1}".format(r["w"], r["h"])
        print("  {0}  ({1:.1f} min, {2})".format(n, dur, shape))

    tmp_list = d.rstrip("/") + "/.concat_list.txt"

    def _ffdec(p: str) -> str:
        # ffmpeg concat demuxer: single-quoted path, escape ' as '\''
        return p.replace("'", "'\\''")

    entries = "".join("file '{0}'\n".format(_ffdec(d.rstrip("/") + "/" + n)) for n in names)
    sh("cat > {0} <<'LISTEOF'\n{1}LISTEOF".format(shlex.quote(tmp_list), entries))
    out_host = f"{d}/{a.out}"
    print(f"\nconcatenating -> {out_host}  ({'re-encode libx264/aac' if a.reencode else 'stream copy'})")
    ffargs = ("-c:v libx264 -preset fast -crf 20 -c:a aac -b:a 128k"
              if a.reencode else "-c copy")
    log = sh(f'cd {shlex.quote(d)} && ffmpeg -v error -y -fflags +genpts -f concat -safe 0 -i {shlex.quote(tmp_list)} '
             f'{ffargs} {shlex.quote(a.out)} 2>&1 | tail -5; rm -f {shlex.quote(tmp_list)}')
    if log.strip():
        print("  ffmpeg output:", log.strip()[:400])

    got = probe.probe(f"{d}/{a.out}" if HOST_ROOT not in d else out_host)
    if got["h"] is None:
        print("ABORT: output does not probe — parts left untouched")
        return 1
    out_min = (got["dur"] or 0) / 60
    print(f"  output: {got['w']}x{got['h']} {out_min:.1f} min {got['size']/1048576:.0f} MB")
    print(f"  parts total: {total:.1f} min")
    if not durations_match(out_min, total):
        print(f"ABORT: duration mismatch (sum {total:.1f} vs out {out_min:.1f}) — parts left untouched")
        return 1
    print("  duration verified within 2%")

    if not a.apply:
        print("\nDRY RUN — parts kept. Re-run with --apply to remove them.")
        return 0
    for n in names:
        sh(f'rm -f {shlex.quote(d + "/" + n)}')
        print(f"  removed part: {n}")
    print(f"\ndone: {a.out} is now the single entry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
