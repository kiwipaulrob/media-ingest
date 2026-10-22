"""Shift a file's audio track by a known offset, verified before it is swapped in.

Why a manual offset: a container check can prove that audio and video timelines
already agree (see media_lib.avsync), but it cannot tell you the *content* is
desynced — that offset lives inside both timelines at once. Attempting to
measure it by correlating on-screen motion against the audio envelope produced
noise-level peaks (r≈0.06, lags scattered over 1.7 s), so the only trustworthy
source for the direction and size is someone watching it.

Positive --offset-ms DELAYS the audio (audio heard too early -> push it later).
Negative advances it (audio heard too late -> pull it earlier).

The swap is deliberately paranoid: the new file is probed and sync-checked
first, and the original is only replaced once that passes.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from media_lib import avsync, config, probe  # noqa: E402


def _run(cmd: str, timeout: int = 3600) -> str:
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", config.MEDIA_HOST,
         f"{config.MEDIA_CT} bash -lc {json.dumps(cmd)}"],
        capture_output=True, text=True, timeout=timeout,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", required=True, help="file on the media host (/mnt/zmedia/...)")
    ap.add_argument("--offset-ms", type=int, required=True,
                    help="positive delays audio, negative advances it")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    src = Path(args.path)
    staging = src.with_suffix(".sync" + src.suffix)
    seconds = args.offset_ms / 1000.0

    print(f"source : {src}")
    print(f"offset : {args.offset_ms:+d} ms ({'delay' if seconds >= 0 else 'advance'} audio)")

    before = avsync.check(str(src))
    print(f"before : {avsync.describe(before)}")

    # `-map 1:a` (all audio, not just the first) and `-map 0:s?` matter: the old
    # `-map 1:a:0` silently dropped every extra audio track AND all subtitles, so a
    # "sync fix" could quietly cost the viewer the commentary or the subs.
    # +faststart is MP4-only; passing it on an mkv just warns.
    faststart = " -movflags +faststart" if src.suffix.lower() == ".mp4" else ""
    cmd = (
        f"{probe.probe_wrap(config.FFMPEG_ENTRY)} -v error -i {shlex.quote(str(src))} "
        f"-itsoffset {seconds} -i {shlex.quote(str(src))} "
        f"-map 0:v:0 -map 1:a -map 0:s? -c:v copy -c:a aac -b:a 320k{faststart} "
        f"-y {shlex.quote(str(staging))}"
    )
    print("remux  : video copied, audio re-encoded with the offset")
    if args.dry_run:
        print("dry-run: not executed")
        return 0

    out = _run(cmd)
    if out.strip():
        print("  ffmpeg:", out.strip()[:200])

    after = avsync.check(str(staging))
    print(f"after  : {avsync.describe(after)}")
    if after.get("verdict") == "fail":
        print("REFUSING: the shifted file does not pass the sync check; original untouched")
        _run(f"rm -f {shlex.quote(str(staging))}")
        return 1

    if not probe.decode_check(str(staging)):
        print("REFUSING: decode sample failed; original untouched")
        _run(f"rm -f {shlex.quote(str(staging))}")
        return 1

    # mv in place keeps the Jellyfin row id and its watch state.
    _run(f"mv -f {shlex.quote(str(staging))} {shlex.quote(str(src))}")
    print(f"swapped: {src.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
