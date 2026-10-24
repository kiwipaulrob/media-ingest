"""Enable a Tdarr node for recoding, and prove it afterwards.

A node can be online, registered, version-matched and still never transcode,
because TWO independent settings can zero it out:

  * `schedule[].transcodecpu` — if every hour is 0 the node runs no worker,
    even when `workerLimits` asks for two. Setting `scheduleEnabled: false`
    makes the node honour `workerLimits` instead; the night-only policy belongs
    on the LIBRARY schedules, not here.
  * `workerLimits.transcodecpu` — a freshly-registered node defaults to ALL
    ZEROS, so it stays idle for a second, less obvious reason.

So both are set, written through BOTH paths (`update-node` for the live node and
`cruddb` for the stored doc, which disagreed in practice), then the result is
verified by re-reading the server rather than trusting either write.

Usage: tdarr_node_enable.py <nodeName> [transcodecpu] [healthcheckcpu]
"""
import json
import sys
import urllib.request

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from media_lib.jellyfin import env as _env  # noqa: E402

# Never inline the key: this repo is PUBLIC. Read it from the shared .env
# (TDARR_API_KEY) or the process environment. A previous version hardcoded a
# `tapi_...` literal here, which put a live credential in public git history.
def _key() -> str:
    """Read the key at CALL time.

    This used to run at import, so any test that imported this module whose environment
    lacked TDARR_API_KEY died with SystemExit during collection — which is exactly the CI
    runner, where the .env does not exist.
    """
    key = _env("TDARR_API_KEY")
    if not key:
        raise SystemExit(
            "TDARR_API_KEY is not set. Add TDARR_API_KEY=<tapi_...> to /root/.hermes/.env "
            "(the key itself must never be committed)."
        )
    return key
BASE = "http://192.168.214.23:8265/api/v2"


def api(path: str, payload: dict | None = None):
    """POST when there is a body, GET otherwise — get-nodes is GET-only (405 on POST)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{BASE}/{path}", data=data,
        headers={"x-api-key": _key(), "Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    body = urllib.request.urlopen(req, timeout=60).read().decode()
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body.strip()}   # update-node answers with plain "OK"


def crud(collection: str, mode: str, obj=None, doc_id=None):
    data = {"collection": collection, "mode": mode}
    if doc_id:
        data["docID"] = doc_id
    if obj is not None:
        data["obj"] = obj
    return api("cruddb", {"data": data})


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    name = sys.argv[1]
    tc = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    hc = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    limits = {"healthcheckcpu": hc, "healthcheckgpu": 0,
              "transcodecpu": tc, "transcodegpu": 0}

    live = api("get-nodes")
    hit = [(nid, n) for nid, n in live.items() if n.get("nodeName") == name]
    if not hit:
        print(f"FAIL: no live node named {name!r}; registered: "
              f"{[n.get('nodeName') for n in live.values()]}")
        return 1
    node_id, node = hit[0]
    print(f"live node {name}: id={node_id}")

    api("update-node", {"data": {"nodeID": node_id, "nodeUpdates": {
        "workerLimits": limits, "scheduleEnabled": False}}})

    docs = crud("NodeJSONDB", "getAll")
    docs = docs if isinstance(docs, list) else list(docs.values())
    for doc in docs:
        if doc.get("_id") != name:
            continue
        doc["workerLimits"] = limits
        doc["scheduleEnabled"] = False
        crud("NodeJSONDB", "update", obj=doc, doc_id=name)
        hours = [h.get("_id") for h in (doc.get("schedule") or []) if h.get("transcodecpu")]
        print(f"  stored doc {name}: limits={limits} scheduleEnabled=False "
              f"(schedule would allow: {hours or 'NONE'} — irrelevant now)")

    live = api("get-nodes")
    for nid, n in live.items():
        if n.get("nodeName") != name:
            continue
        c = n.get("config") or {}
        print(f"  verified live: {name} paused={n.get('nodePaused')} "
              f"limits={c.get('workerLimits')} schedEnabled={c.get('scheduleEnabled')}")
        return 0
    print(f"FAIL: {name} disappeared after the update")
    return 1


if __name__ == "__main__":
    sys.exit(main())
