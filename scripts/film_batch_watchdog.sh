#!/bin/bash
# Watchdog for a film batch: keep the ingest wave loop alive and finish the job.
#
# Silent unless there is news (empty stdout => the cron delivery sends nothing):
#   * restarts the wave loop if it died while downloads are still unfinished
#   * prints ONE final line when every wanted release is placed in the library
#
#   bash scripts/film_batch_watchdog.sh [batch.json] [library]
set -u
cd "$(dirname "$0")/.." || exit 1
BATCH="${1:-state/film-queued.json}"
LIB="${2:-Film Noir}"
DONE_MARK="state/.film-batch-done"

[ -f "$DONE_MARK" ] && exit 0

read -r left moving <<<"$(python3 scripts/film_batch_status.py --batch "$BATCH" --remaining 2>/dev/null || echo "0 0")"
have_ingest=$(pgrep -f "cli.ingest_new" | head -1)
have_loop=$(pgrep -f "film_ingest_wave.sh" | head -1)

if [ "${left:-0}" -gt 0 ] && [ -z "$have_loop" ] && [ -z "$have_ingest" ]; then
  nohup bash scripts/film_ingest_wave.sh "$BATCH" "$LIB" 30 >>state/film-ingest-waves.log 2>&1 &
  echo "film batch: wave loop was not running with $left release(s) unfinished — restarted it"
fi

# Row check scoped to batch folders (tmdbid- tagged), applied only when idle.
if [ -z "$have_ingest" ]; then
  python3 scripts/film_rows_reconcile.py --library "$LIB" --only "tmdbid-" --apply >>state/film-rows.log 2>&1
fi

if [ "${left:-0}" -eq 0 ] && [ -z "$have_ingest" ]; then
  placed=$(python3 - "$BATCH" <<'PY'
import json, sys
sys.path.insert(0, '.')
from media_lib import jellyfin, place
libs = jellyfin.get_json("/Library/VirtualFolders") or []
lid = next((l["ItemId"] for l in libs if l.get("Name") == "Film Noir"), "")
d = jellyfin.get_json(f"/Items?ParentId={lid}&Recursive=true&IncludeItemTypes=Movie"
                      "&Fields=Path&Limit=500") or {}
print(sum(1 for i in (d.get("Items") or []) if "tmdbid-" in (i.get("Path") or "")))
PY
)
  touch "$DONE_MARK"
  echo "film batch complete: all releases downloaded; $placed new film item(s) in the library."
fi
exit 0
