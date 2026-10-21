"""Stage 1 — scan.

Reads the downloader queue and probes only the files that could matter, then
writes ONE scan artefact. Nothing is changed by this command.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from media_lib import config, library, probe, qb, safety


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scan completed downloads (read-only)")
    ap.add_argument("--out", default=None, help="scan artefact path")
    ap.add_argument(
        "--no-dump", action="store_true", help="reuse the cached library dump"
    )
    args = ap.parse_args(argv)

    config.ensure_state_dir()
    ledger = safety.load_ledger()
    known = set(ledger)

    rows, op = qb.completed_candidates(known)
    print(f"torrents complete and not yet ledgered: {len(rows)}", flush=True)

    entries = []
    for t in rows:
        vids = qb.video_files(t, op=op)
        if not vids:
            continue  # audio / non-video: not our domain
        entries.append(
            {
                "hash": t.get("hash"),
                "name": t.get("name") or "",
                "save_path": t.get("save_path"),
                "size": vids[0]["size"],
                "src": vids[0]["remote"],
                "videos_in_torrent": len(vids),
                "all_videos": [v["remote"] for v in vids],
            }
        )

    # Probe every candidate's largest file in ONE remote pass (probe.probe is
    # per-file; call it only for entries missing from the scan cache).
    for e in entries:
        p = probe.probe(e["src"])
        e["probe"] = p
        e["head"] = "ok"
        try:
            head = probe.head_bytes(e["src"], 16)
            e["head"] = probe.container_from_head(head)
        except Exception as exc:  # noqa: BLE001 - report, never guess
            e["head"] = f"ERR:{type(exc).__name__}"

    dump = None if args.no_dump else library.load()
    out = {
        "scanned_at": int(time.time()),
        "entries": entries,
        "dump_count": (dump or {}).get("count"),
    }
    path = Path(args.out or (config.STATE_DIR / "scan.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, sort_keys=True))

    print(f"scan written: {path} ({len(entries)} candidate(s))")
    for e in entries[:10]:
        p = e["probe"]
        res = (
            f"{p.get('w')}x{p.get('h')} {p.get('codec')}"
            if p.get("h")
            else "PROBE-FAIL"
        )
        print(f"  {e['hash'][:12]}  {res:<24} {e['head']:<10} {e['name'][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
