#!/usr/bin/env bash
# Tranche 2: the four-parters. Waits for the running tranche to finish so library scans don't overlap.
set -u
cd /root/.hermes/profiles/media-maintainer/media-ops || exit 1
set -a; . /root/.hermes/.env 2>/dev/null; set +a

NODE="root@192.168.214.131"

# wait for tranche 1 (up to 40 minutes)
for _ in $(seq 1 240); do
  if ! pgrep -f "scripts/batch_series_run.sh" >/dev/null 2>&1; then break; fi
  sleep 10
done
echo "tranche 1 finished or timed out — starting tranche 2"

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
    timeout 1400 python3 -u scripts/multipart_to_series.py 2>&1 | tail -28
}

run_one "*Century of the Self*"        "The Century of the Self"        2002 "The Century of the Self (2002)"
run_one "*Mayfair Set*"                "The Mayfair Set"                1999 "The Mayfair Set (1999)"
run_one "*Devil*Music*"                "The Devil's Music"              2009 "The Devil's Music (2009)"
run_one "*Stax*"                       "Stax: Soulsville USA"           2024 "Stax: Soulsville USA (2024)"
run_one "*My Life as a Rolling Stone*" "My Life as a Rolling Stone"     2022 "My Life as a Rolling Stone (2022)"
echo ""
echo "TRANCHE 2 COMPLETE"
