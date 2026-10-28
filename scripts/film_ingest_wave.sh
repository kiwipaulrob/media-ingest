#!/bin/bash
# Keep placing a FILM batch into the library as its downloads complete.
#
# Runs the SHIPPED pipeline in waves — scan -> plan -> ingest_new --from-plan —
# until every wanted release is placed or nothing is still moving, so a batch
# does not depend on an agent staying awake. The only batch-specific step is
# `film_batch_ids.py`, which answers "which approved rows are mine?"; the
# placement itself (dest naming, [tmdbid-N] tag, copy/probe/duration/decode
# gates, ledger) is entirely `cli.ingest_new`.
#
# Serialised: a wave never starts while another ingest is mid-flight (two intake
# processes at once corrupt the ledger view the scan excludes on).
#
#   bash scripts/film_ingest_wave.sh [batch.json] [library] [max_waves]
set -u
cd "$(dirname "$0")/.." || exit 1
BATCH="${1:-state/film-queued.json}"
LIB="${2:-Film Noir}"
MAXWAVES="${3:-30}"
LOG="state/film-ingest-waves.log"
STATE_DIR="/root/.hermes/profiles/media-maintainer/state"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

log "wave loop start: batch=$BATCH library=$LIB"
for w in $(seq 1 "$MAXWAVES"); do
  # Wait for any other ingest to finish (bounded, so a wedged run cannot hang us).
  for _ in $(seq 1 80); do
    pgrep -f "cli.ingest_new" >/dev/null || break
    sleep 30
  done

  python3 -m cli.scan >>"$LOG" 2>&1
  python3 -m cli.plan --scan "$STATE_DIR/scan.json" --out state/plan-films.json >>"$LOG" 2>&1
  ids=$(python3 scripts/film_batch_ids.py --batch "$BATCH" 2>>"$LOG")
  log "wave $w: approved ids [${ids:-none}]"

  if [ -n "$ids" ]; then
    python3 -m cli.ingest_new --from-plan "$STATE_DIR/needs-your-check.json" \
        --ids "$ids" --library "$LIB" >>"$LOG" 2>&1
  fi

  read -r left moving <<<"$(python3 scripts/film_batch_status.py --batch "$BATCH" --remaining)"
  log "wave $w done: $left release(s) unfinished, $moving actively downloading"
  if [ -z "$ids" ] && [ "$moving" -eq 0 ]; then
    log "nothing approved and nothing downloading — stopping"
    break
  fi
  sleep 600
done
log "wave loop finished"

