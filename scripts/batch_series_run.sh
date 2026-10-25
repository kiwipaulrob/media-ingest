#!/usr/bin/env bash
# Run multipart_to_series across several titles, discovering each source folder on the host.
set -u
cd /root/.hermes/profiles/media-maintainer/media-ops || exit 1
set -a; . /root/.hermes/.env 2>/dev/null; set +a

NODE="root@192.168.214.131"

run_one() {
  local pattern="$1" query="$2" year="$3" title="$4"
  echo ""
  echo "=============================================================="
  echo "TITLE: $title   (pattern: $pattern)"
  echo "=============================================================="
  local dir
  dir=$(timeout 200 ssh -o ConnectTimeout=12 "$NODE" \
        "find /mnt/zmedia/videos -maxdepth 3 -type d -iname '$pattern' 2>/dev/null | head -1" | tr -d '\r')
  if [ -z "$dir" ]; then
    echo "SKIP: no source folder matching '$pattern'"
    return 0
  fi
  echo "SRC: $dir"
  SRC="$dir" QUERY="$query" YEAR="$year" TITLE="$title" \
    timeout 1400 python3 -u scripts/multipart_to_series.py 2>&1 | tail -30
}

run_one "*Can*t Get You Out of My Head*" "Can't Get You Out of My Head" 2021 "Can't Get You Out of My Head (2021)"
run_one "*Pandora*Box*"                  "Pandora's Box"               1992 "Pandora's Box (1992)"
run_one "*American Revolution*"          "The American Revolution"      2025 "The American Revolution (2025)"
echo ""
echo "BATCH LOOP COMPLETE"
