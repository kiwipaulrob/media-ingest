#!/usr/bin/env bash
# Daily ZFS snapshot of zmedia, keep 28 days.
#
# Why this exists: a logical delete (a library row deleted through Jellyfin) destroyed 5.3 GB of media and
# ZFS checksum repair could not help — the unlink was valid at the filesystem level. A snapshot makes that
# recovery a copy from .zfs/snapshot instead of a re-download.
#
# There is only ONE dataset (zmedia), so this snapshots everything including downloads. Media is largely
# static and only changed blocks are retained, so the cost stays small.
set -u

DS="zmedia"
KEEP_DAYS=28
PREFIX="auto-daily"
LOG="/var/log/zmedia-snapshot.log"
STAMP="$(date +%Y-%m-%d-%H%M)"

log() { printf '%s %s\n' "$(date -Is)" "$*" >> "$LOG"; }

if ! zpool list -H -o health "$DS" 2>/dev/null | grep -q ONLINE; then
    log "SKIP: pool/dataset $DS is not ONLINE — not snapshotting an unhealthy pool"
    exit 1
fi

if ! zfs snapshot "${DS}@${PREFIX}-${STAMP}"; then
    log "ERROR: snapshot ${DS}@${PREFIX}-${STAMP} failed"
    exit 1
fi
log "created ${DS}@${PREFIX}-${STAMP}"

# prune: destroy our own snapshots older than KEEP_DAYS
cutoff=$(( $(date +%s) - KEEP_DAYS * 86400 ))
zfs list -H -p -t snapshot -o name,creation -s creation 2>/dev/null \
  | while IFS=$'\t' read -r name created; do
        case "$name" in
            "${DS}@${PREFIX}-"*) ;;
            *) continue ;;
        esac
        if [ "$created" -lt "$cutoff" ]; then
            if zfs destroy "$name"; then log "pruned $name"; else log "ERROR: could not destroy $name"; fi
        fi
    done

log "done: $(zfs list -H -t snapshot -o name 2>/dev/null | grep -c "^${DS}@${PREFIX}-") snapshots retained"
