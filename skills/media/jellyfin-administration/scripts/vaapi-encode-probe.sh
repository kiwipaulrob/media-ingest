#!/bin/bash
# VAAPI hardware-encode probe: prove which codecs a GPU can ACTUALLY encode.
# Run INSIDE the media container (needs ffmpeg + /dev/dri):
#   docker exec jellyfin bash -c 'bash -s' < scripts/vaapi-encode-probe.sh
# Or with a custom device: DEV=/dev/dri/renderD129 bash scripts/vaapi-encode-probe.sh
#
# Expected on Intel N150 (Alder Lake-N): h264 OK, hevc OK, av1 FAIL (decode-only).
# A non-zero output file = encoder works. Do NOT trust Jellyfin UI checkboxes;
# decode support never implies encode support.
set -u
DEV="${DEV:-/dev/dri/renderD128}"
CODECS="${CODECS:-h264 hevc av1}"
FFMPEG="$(command -v ffmpeg || echo /usr/lib/jellyfin-ffmpeg/ffmpeg)"

if [ ! -e "$DEV" ]; then echo "device $DEV not found"; exit 1; fi
if [ ! -x "$FFMPEG" ]; then echo "no ffmpeg found"; exit 1; fi

echo "== VAAPI encode probe on $DEV =="
for c in $CODECS; do
  out="/tmp/probe_${c}.mp4"
  err="/tmp/probe_${c}.err"
  if "$FFMPEG" -v error -vaapi_device "$DEV" -f lavfi \
      -i 'testsrc=size=640x360:rate=30:duration=2' \
      -vf 'format=nv12,hwupload' -c:v "${c}_vaapi" -f mp4 "$out" 2>"$err"; then
    size=$(stat -c%s "$out" 2>/dev/null || echo 0)
    echo "${c}_vaapi: OK ($size bytes)"
  else
    echo "${c}_vaapi: FAIL"
    grep -iE 'entrypoint|conversion failed|error' "$err" | head -3
  fi
  rm -f "$out" "$err"
done
