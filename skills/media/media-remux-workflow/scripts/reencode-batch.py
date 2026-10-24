#!/usr/bin/env python3
"""Batch re-encode of transcode-forcing legacy-codec files to H.264.

Deterministic, no agent judgement mid-run. Every file goes through the same
gate; NOTHING is deleted or swapped except proven gate-passers.

Per file:
  1. ffprobe streams (video must be legacy mpeg4/mpeg2/mpeg1/msmpeg4/wmv3/vp8
     to qualify; h264/hevc/vp9/av1 are skipped as already direct-play).
  2. ffmpeg libx264 CRF 20 preset medium, audio stream-copied
     (mp3/ac3/dts all kept — browsers decode mp3, MKV holds the rest),
     -err_detect ignore_err + +genpts so damaged sections encode
     with glitches instead of aborting.
  3. Full-file decode of the OUTPUT with -xerror (fatal on any error),
     plus duration within 2s of the source.
  4. PASS -> park original to --park-dir, rename temp to canonical name.
     FAIL -> temp deleted, original untouched, file listed for replacement hunt.

Sidecars (.nfo/.srt/.jpg/.png/.tbn) stay in place — the swap keeps the same
basename (non-MKV sources take the .mkv name; that extension swap IS a new
Jellyfin item, so report it).

Usage:
  python3 reencode-batch.py --scan <libdir> --park-dir <dir> [--dry-run] [--limit N]

Exit codes: 0 all passed (or dry-run), 1 one or more files failed the gate.
"""
import argparse
import os
import shutil
import subprocess
import sys

VID_EXTS = (".mkv", ".mp4", ".avi", ".ts", ".m4v", ".divx")
SKIP_SUFFIXES = (".reenc-tmp.mkv", ".remux-tmp.mkv", ".remux.mkv")
LEGACY_VIDEO = {"mpeg4", "mpeg2video", "mpeg1video", "msmpeg4v3", "wmv3", "vp8"}
TMP_SUFFIX = ".reenc-tmp.mkv"


def probe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=60)
    v, a = [], []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 2:
            continue
        codec, typ = parts
        if typ == "video":
            v.append(codec)
        elif typ == "audio":
            a.append(codec)
    return v, a


def duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", required=True)
    ap.add_argument("--park-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cands = []
    for root, _, files in os.walk(args.scan):
        for f in sorted(files):
            if not f.lower().endswith(VID_EXTS):
                continue
            if f.endswith(SKIP_SUFFIXES):
                continue
            p = os.path.join(root, f)
            try:
                v, _ = probe(p)
            except Exception as e:
                print("PROBE-ERR %s :: %s" % (p, e), flush=True)
                continue
            if any(c in LEGACY_VIDEO for c in v):
                cands.append((p, "/".join(v)))
    print("CANDIDATES %d" % len(cands), flush=True)
    for p, v in cands:
        print("  FLAG %-12s %5.2fGB %s" % (v, os.path.getsize(p) / 1e9, p),
              flush=True)
    if args.dry_run:
        print("DRY-RUN \u2014 no encodes, no swaps", flush=True)
        return 0
    if args.limit:
        cands = cands[:args.limit]

    os.makedirs(args.park_dir, exist_ok=True)
    passed, failed = [], []
    for src, vcodecs in cands:
        tmp = os.path.splitext(src)[0] + TMP_SUFFIX
        dur0 = duration(src)
        q = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-err_detect", "ignore_err", "-i", src, "-map", "0",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "copy",
             "-fflags", "+genpts", tmp],
            capture_output=True, text=True, timeout=14400)
        if q.returncode != 0:
            print("ENCODE-FAIL %s :: %s" % (src, (q.stderr or "")[-200:]),
                  flush=True)
            if os.path.exists(tmp):
                os.remove(tmp)
            failed.append(src)
            continue
        v = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-xerror", "-i", tmp, "-f", "null", "-"],
            capture_output=True, text=True, timeout=14400)
        if v.returncode != 0:
            print("VERIFY-FAIL %s :: %s" % (src, (v.stderr or "")[-200:]),
                  flush=True)
            os.remove(tmp)
            failed.append(src)
            continue
        dur1 = duration(tmp)
        if dur0 and dur1 and abs(dur0 - dur1) > 2:
            print("DURATION-MISMATCH %s %.0f vs %.0f" % (src, dur0, dur1),
                  flush=True)
            os.remove(tmp)
            failed.append(src)
            continue
        final = src if src.lower().endswith(".mkv") else \
            os.path.splitext(src)[0] + ".mkv"
        shutil.move(src, os.path.join(args.park_dir, os.path.basename(src)))
        if os.path.exists(final) and final != src:
            print("UNEXPECTED-EXISTS %s \u2014 abort swap, original parked" % final,
                  flush=True)
            failed.append(src)
            continue
        os.rename(tmp, final)
        print("PASS %s -> %s" % (src, final), flush=True)
        passed.append(final)

    print("BATCH-DONE passed=%d failed=%d" % (len(passed), len(failed)),
          flush=True)
    for p in failed:
        print("  NEEDS-REPLACEMENT-HUNT %s" % p, flush=True)
    print("NEXT: library-scoped Refresh of the touched librar%s, then per-item "
          "full refresh where overview/art did not carry; recount imageless." %
          ("y" if len(passed) < 2 else "ies"), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
