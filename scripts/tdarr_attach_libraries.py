"""Put the two music libraries on the Tdarr flow — the update that actually persists.

Two mistakes this script exists to avoid repeating:

1. `cruddb` `update` requires a `docID` field SEPARATE from `obj`. Passing the full
   document (with `_id` inside) and no `docID` returns 200 and changes nothing, so a
   "transcodes False -> True" printed by the caller was only a local variable change.
2. `cache` must be set on the library or the node refuses to start any flow with
   "Transcode Cache is not set" — it mounts the transcode cache at `/temp`, so the
   library cache must be exactly `/temp`.

Verification is by RE-READING the library from the server, never by trusting the write.
"""
import json
import sys
import urllib.request

KEY = "tapi_40c41a4b3245a315d16153d692a264f8"
BASE = "http://192.168.214.23:8265/api/v2"
FLOW_ID = "b3VtKqt"
TARGETS = ("musicdocos", "musicdocos-series")
CACHE = "/temp"


def api(path: str, payload: dict):
    req = urllib.request.Request(
        f"{BASE}/{path}", data=json.dumps(payload).encode(),
        headers={"x-api-key": KEY, "Content-Type": "application/json"}, method="POST",
    )
    body = urllib.request.urlopen(req, timeout=60).read().decode()
    return json.loads(body) if body.strip() else {}


def crud(collection: str, mode: str, obj=None, doc_id=None):
    data = {"collection": collection, "mode": mode}
    if doc_id is not None:
        data["docID"] = doc_id
    if obj is not None:
        data["obj"] = obj
    return api("cruddb", {"data": data})


def main() -> int:
    libs = crud("LibrarySettingsJSONDB", "getAll")
    libs = libs if isinstance(libs, list) else list(libs.values())

    for lib in libs:
        if lib.get("name") not in TARGETS:
            continue
        doc_id = lib.get("_id")
        before = (lib.get("processTranscodes"), lib.get("cache"), lib.get("flowId"))
        lib["processTranscodes"] = True
        lib["cache"] = CACHE
        lib["flowId"] = FLOW_ID
        lib["decisionMaker"] = {"settingsFlows": False}
        crud("LibrarySettingsJSONDB", "update", obj=lib, doc_id=doc_id)
        print(f"  {lib.get('name'):<20} docID={doc_id}  {before} -> "
              f"({True}, {CACHE}, {FLOW_ID})")

    print("\nre-reading from the server to prove it persisted:")
    libs = crud("LibrarySettingsJSONDB", "getAll")
    libs = libs if isinstance(libs, list) else list(libs.values())
    ok = True
    for lib in libs:
        if lib.get("name") not in TARGETS:
            continue
        tc, cache, flow = lib.get("processTranscodes"), lib.get("cache"), lib.get("flowId")
        good = tc is True and cache == CACHE and flow == FLOW_ID
        ok = ok and good
        hours = len([h for h in (lib.get("schedule") or []) if h.get("checked")])
        print(f"  {'OK ' if good else 'FAIL'} {lib.get('name'):<20} "
              f"transcodes={tc} cache={cache!r} flow={flow} scheduled_hours={hours}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
