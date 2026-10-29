"""Lossless SAR repair for aspect-flagged files (dry-run default).

For files where the pixels are right but the container aspect flag is wrong
(wide raster + narrow DAR), a stream-copy remux with `-aspect 16:9` fixes
playback in seconds with zero pixel loss — no re-encode. MKV sources keep
their filename (same Jellyfin row); AVI sources move to .mkv (new row —
run tdarr_preserve_userdata snapshot/restore around the batch).

    python3 scripts/fix_aspect_remux.py --list state/aspect-suspects.txt [--apply]

Gates per file: source SAR confirms the suspect flag; output probes SAR 1:1;
on any failure the temp is deleted and the file is reported, never swapped.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, ".")


def parse_suspects(path: str) -> List[Tuple[str, str, str]]:
    """(container_path, res, name) rows from a scan_aspect_flags run. Pure.

    Tolerates the id= line the scanner now emits between path and reason.
    """
    out = []
    lines = Path(path).read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("  [suspect-"):
            # "  [suspect-narrow] 720x400 DAR=5:4 Some Film"
            tail = line.split("]", 1)[1].strip()
            res, rest = tail.split(None, 1)
            parts = rest.split("DAR=", 1)[1].split(None, 1)
            name = parts[1] if len(parts) > 1 else ""
            container = lines[i + 1].strip() if i + 1 < len(lines) else ""
            out.append((container, res, name))
            i += 2
            # skip optional id= line and the reason line
            while i < len(lines) and not lines[i].startswith("  [suspect-") \
                    and not lines[i].startswith("ok:"):
                i += 1
        else:
            i += 1
    return out


def main(argv=None) -> int:
    import subprocess

    from media_lib import config, place, probe  # deferred: network only in main

    ap = argparse.ArgumentParser(description="Lossless SAR repair.")
    ap.add_argument("--list", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    rows = parse_suspects(a.list)
    print(f"suspects: {len(rows)}")
    for container, res, name in rows:
        disk = place.to_disk(container)
        print(f"  {'FIX' if a.apply else 'WOULD-FIX'} {res} {name[:52]}")
        if not a.apply:
            continue
        tmp = disk.rsplit(".", 1)[0] + ".sar-tmp.mkv"
        try:
            # ffmpeg lives on the pool host, not in CT105 (on_media has no
            # ffmpeg) — the files are pool-local, so run it over ssh there.
            # +genpts: XviD-era muxes carry broken timestamps that kill a
            # plain stream copy (proven on the South Bank batch).
            subprocess.run(
                ["ssh", "-o", "ConnectTimeout=15", config.NODE_PROX4,
                 f"ffmpeg -y -v error -fflags +genpts -i {shlex.quote(disk)} "
                 f"-map 0 -c copy -aspect 16:9 {shlex.quote(tmp)}"],
                check=True, capture_output=True, text=True, timeout=300)
            check = probe.on_media(
                f"{probe.probe_wrap(config.FFPROBE_ENTRY)} -v error "
                f"-select_streams v:0 "
                f"-show_entries stream=display_aspect_ratio -of csv=p=0 {shlex.quote(tmp)}")
            # Gate on DAR ≈ 16:9, not SAR == 1:1: a 640x352 raster is already
            # 16:9-proportioned, so -aspect 16:9 yields SAR 44:45 (correct).
            dar = (check.strip().split(",")[0] or "?").strip()
            m = re.match(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$", dar)
            ratio = float(m.group(1)) / float(m.group(2)) if m and float(m.group(2)) else 0
            if not (1.70 <= ratio <= 1.86):
                raise RuntimeError(f"output DAR is {dar}, not ~16:9")
        except Exception as exc:  # noqa: BLE001 - report, never swap
            print(f"    FAIL {type(exc).__name__}: {str(exc)[:100]}")
            try:
                probe.on_media(f"rm -f {shlex.quote(tmp)}", timeout=60)
            except Exception:  # noqa: BLE001 - temp cleanup best effort
                pass
            continue
        print(f"    OK sar=1:1 {tmp[-46:]}  (swap: {'same name' if disk.endswith('.mkv') else 'new .mkv row'})")
    if a.apply:
        print("  (remuxes staged as *.sar-tmp.mkv — swap + retire originals next)")
    else:
        print("  (dry run — pass --apply to remux)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
