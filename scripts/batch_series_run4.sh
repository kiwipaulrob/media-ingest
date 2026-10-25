#!/usr/bin/env bash
# Tranche 4: four-parters and film-subject strands.
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

run_one "*House Through Time*"      "A House Through Time"        2018 "A House Through Time (2018)"
run_one "*Cinema Europe*"           "Cinema Europe: The Other Hollywood" 1995 "Cinema Europe (1995)"
run_one "*Lion Roars*"              "MGM: When the Lion Roars"    1992 "MGM: When the Lion Roars (1992)"
run_one "*Hendrix*Uncut*"           "Jimi Hendrix: The Uncut Story" 2004 "Jimi Hendrix The Uncut Story (2004)"
run_one "*British Pathe*"           "The Story of British Pathe"  2011 "The Story of British Pathe (2011)"
echo ""
echo "TRANCHE 4 COMPLETE"
