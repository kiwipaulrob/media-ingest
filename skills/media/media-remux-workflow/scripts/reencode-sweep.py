#!/usr/bin/env python3
"""Scan a library tree for transcode-forcing legacy video codecs.
Read-only: probes streams, prints candidates, exits 0. No encodes, no writes.
Usage: python3 reencode-sweep.py --scan <libdir>
"""
import argparse, os, subprocess, sys

VID_EXTS = (".mkv", ".mp4", ".avi", ".ts", ".m4v", ".divx")
SKIP_SUFFIXES = (".reenc-tmp.mkv", ".remux-tmp.mkv", ".remux.mkv")
LEGACY_VIDEO = {"mpeg4", "mpeg2video", "mpeg1video", "msmpeg4v3", "wmv3", "vp8"}


def probe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=60)
    vcodecs, acodecs = [], []
    for line in r.stdout.strip().splitlines():
        # rows look like: mpeg4,video  or  mp3,audio
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 2:
            continue
        codec, typ = parts
        if typ == "video":
            vcodecs.append(codec)
        elif typ == "audio":
            acodecs.append(codec)
    return vcodecs, acodecs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", required=True)
    args = ap.parse_args()
    total, flagged = 0, 0
    for root, _, files in os.walk(args.scan):
        for f in sorted(files):
            if not f.lower().endswith(VID_EXTS):
                continue
            if f.endswith(SKIP_SUFFIXES):
                continue
            p = os.path.join(root, f)
            total += 1
            try:
                v, _ = probe(p)
            except Exception as e:
                print("PROBE-ERR %s :: %s" % (p, e), flush=True)
                continue
            if any(c in LEGACY_VIDEO for c in v):
                sz = os.path.getsize(p) / 1e9
                flagged += 1
                print("FLAG %-12s %5.2fGB %s" % ("/".join(v), sz, p), flush=True)
    print("SCAN-DONE total=%d flagged=%d" % (total, flagged), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
