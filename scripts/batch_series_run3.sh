#!/usr/bin/env bash
# Tranche 3: three-parters, mostly Adam Curtis and BBC arts strands.
set -u
cd /root/.hermes/profiles/media-maintainer/media-ops || exit 1
set -a; . /root/.hermes/.env 2>/dev/null; set +a

NODE="root@192.168.214.132"

run_one() {
  local pattern="$1" query="$2" year="$3" title="$4"
  echo ""
  echo "=============================================================="
  echo "TITLE: $title"
  echo "=============================================================="
  local dir
  dir=$(timeout 200 ssh -o ConnectTimeout=12 "$NODE" \
        "find /mnt/zmedia/videos -maxdepth 3 -type d -iname '$pattern' 2>/dev/null | head -1" | tr -d '\r')
  if [ -z "$dir" ]; then echo "SKIP: no folder matching '$pattern'"; return 0; fi
  echo "SRC: $dir"
  SRC="$dir" QUERY="$query" YEAR="$year" TITLE="$title" \
    timeout 1400 python3 -u scripts/multipart_to_series.py 2>&1 | tail -26
}

run_one "*Power of Nightmares*"            "The Power of Nightmares"        2004 "The Power of Nightmares (2004)"
run_one "*The Trap*"                       "The Trap: What Happened to Our Dream of Freedom" 2007 "The Trap (2007)"
run_one "*All Watched Over*"               "All Watched Over by Machines of Loving Grace" 2011 "All Watched Over by Machines of Loving Grace (2011)"
run_one "*Comics Britannia*"               "Comics Britannia"               2007 "Comics Britannia (2007)"
run_one "*Designing the Decades*"          "Designing the Decades"          2016 "Designing the Decades (2016)"
echo ""
echo "TRANCHE 3 COMPLETE"
