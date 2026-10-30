"""Queue explicit files into a Tdarr flow (dry-run default).

Suite-queues-per-file direction of the Tdarr integration: the suite decides
WHICH files (aspect suspects, audio-compat list, legacy picks) and Tdarr
executes. No bulk switches, no library links — every file id is explicit.

    python3 scripts/tdarr_queue.py --flow audio --files /data/a/x.mkv [...] [--apply]

Flows (house): audio=j2PAq5X (AAC stereo first), sar=HxM2kjc (16:9 remux),
legacy=b3VtKqt (SD->h264). Files must already be Tdarr records (scan the
library first); unknown ids refuse. After the run, verify with
media_recode_status and the suite's own gates — Tdarr replacing bytes never
re-applies metadata or watch state (lesson 41).
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

FLOWS = {
    "audio": "j2PAq5X",
    "sar": "HxM2kjc",
    "legacy": "b3VtKqt",
}


def queue_payload(file_id: str, flow_id: str) -> Dict[str, object]:
    """bulk-update-files body for one file. Pure."""
    return {
        "fileIds": [file_id],
        "updatedObj": {
            "flowId": flow_id,
            "TranscodeDecisionMaker": "Queued",
            "bumped": True,
            "lastTranscodeDate": 0,
            "holdUntil": 0,
        },
    }


def main(argv=None, call_api=None) -> int:
    ap = argparse.ArgumentParser(description="Queue explicit files into a Tdarr flow.")
    ap.add_argument("--flow", required=True, choices=sorted(FLOWS))
    ap.add_argument("--files", nargs="+", required=True,
                    help="explicit container paths, e.g. /data/musicdocos/x.mkv")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if call_api is None:
        from tdarr_kick import api as _tdarr_api  # deferred: needs TDARR_API_KEY

        call_api = _tdarr_api

    flow_id = FLOWS[a.flow]
    known: Dict[str, bool] = {}
    try:
        from tdarr_kick import crud as _crud  # deferred: needs TDARR_API_KEY

        docs = _crud("FileJSONDB", "getAll")
        docs = docs if isinstance(docs, list) else list(docs.values())
        known = {str(d.get("_id")): True for d in docs if isinstance(d, dict)}
    except Exception as exc:  # noqa: BLE001 - report, never queue blind
        print(f"  cannot list Tdarr records: {type(exc).__name__} — refusing")
        return 1
    missing = [f for f in a.files if f not in known]
    if missing:
        print(f"  refusing: {len(missing)} file(s) are not Tdarr records:")
        for f in missing[:10]:
            print(f"    {f}")
        return 2
    print(f"flow {a.flow} ({flow_id}): {len(a.files)} file(s)")
    if not a.apply:
        for f in a.files:
            print(f"  WOULD-QUEUE {f}")
        print("  (dry run — pass --apply to queue)")
        return 0
    n = 0
    for f in a.files:
        try:
            call_api("bulk-update-files", {"data": queue_payload(f, flow_id)})
            n += 1
            print(f"  QUEUED {f}")
        except Exception as exc:  # noqa: BLE001 - report, keep going
            print(f"  FAILED {f}: {type(exc).__name__}")
    print(f"  queued {n}/{len(a.files)} — verify with media_recode_status")
    return 0 if n == len(a.files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
