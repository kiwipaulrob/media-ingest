"""Bring the Tdarr node back to life and put the two music libraries on the flow.

Root cause found by inspection, not guesswork: the node `prox4` was registered with
`workerLimits.transcodecpu = 2`, but its 24-hour `schedule` was ZERO in every hour,
so the node ran no transcode worker at all — for 11 days. Libraries, flow and
registration were all correct; the timetable was the kill switch.

Fix: set `scheduleEnabled=false` so the node simply honours `workerLimits`. The
night-only policy is NOT lost — it lives on each LIBRARY's own schedule (23:00-05:00),
which gates when work is issued. Then point the two music libraries at the house flow
so the legacy backlog can run.

Nothing here enables transcoding on a library that has not already been proven; the
`_FLOWTEST one-file proof` library stays the place a flow is validated.
"""
import json
import sys
import urllib.request

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from media_lib.jellyfin import env as _env  # noqa: E402

# Never inline the key: this repo is PUBLIC. Read it from the shared .env
# (TDARR_API_KEY) or the process environment. A previous version hardcoded a
# `tapi_...` literal here, which put a live credential in public git history.
KEY = _env("TDARR_API_KEY")
if not KEY:
    raise SystemExit(
        "TDARR_API_KEY is not set. Add TDARR_API_KEY=<tapi_...> to /root/.hermes/.env "
        "(the key itself must never be committed)."
    )
BASE = "http://192.168.214.23:8265/api/v2"
NODE_ID = "prox4"
FLOW_ID = "b3VtKqt"          # "Legacy SD -> h264 CRF20 (software, house)"
ENABLE_LIBS = {"musicdocos", "musicdocos-series"}


def api(path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{BASE}/{path}", data=data,
        headers={"x-api-key": KEY, "Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=60) as fh:
        body = fh.read().decode()
    return json.loads(body) if body.strip() else {}


def cruddb(collection: str, mode: str, obj=None):
    data = {"collection": collection, "mode": mode}
    if obj is not None:
        data["obj"] = obj
    return api("cruddb", {"data": data})


def main() -> int:
    nodes = cruddb("NodeJSONDB", "getAll")
    node = (nodes[0] if isinstance(nodes, list) else list(nodes.values())[0])
    print(f"node {node.get('_id')}: scheduleEnabled={node.get('scheduleEnabled')} "
          f"workerLimits={node.get('workerLimits')}")
    hours_on = [h.get("_id") for h in (node.get("schedule") or []) if h.get("transcodecpu")]
    print(f"  hours that actually run a transcode worker: {hours_on or 'NONE'}")

    node["scheduleEnabled"] = False
    cruddb("NodeJSONDB", "update", node)
    print("  -> scheduleEnabled=false (node honours workerLimits; library schedules still gate work)")

    libs = cruddb("LibrarySettingsJSONDB", "getAll")
    libs = libs if isinstance(libs, list) else list(libs.values())
    for lib in libs:
        if lib.get("name") in ENABLE_LIBS:
            was = lib.get("processTranscodes")
            lib["processTranscodes"] = True
            lib["flowId"] = FLOW_ID
            lib["decisionMaker"] = {"settingsFlows": False}
            cruddb("LibrarySettingsJSONDB", "update", lib)
            hours = [h.get("_id") for h in (lib.get("schedule") or []) if h.get("checked")]
            print(f"  {lib.get('name')}: transcodes {was} -> True, flow={FLOW_ID}, "
                  f"{len(hours)} scheduled hour(s)")

    # Re-read through the live endpoint, not the DB, to prove the node picked it up.
    live = api("get-nodes")
    for nid, n in live.items():
        lim = (n.get("config") or {}).get("workerLimits") or {}
        wk = n.get("workers") or {}
        counts = {k: len(v or []) for k, v in wk.items()} if wk else {}
        print(f"  live node {n.get('nodeName')}: limits={lim} workers={counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
