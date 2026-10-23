"""Start recoding outside the scheduled window, and unstick previously-failed files.

Two separate gates stop Tdarr from working, and both must be opened for an
on-demand run:

  1. Each LIBRARY has a `schedule` of hour blocks (`{"_id": "Fri:19-20", "checked": false}`).
     Outside those hours the library issues no work, even though the nodes are idle
     and the files sit queued. This opens the current hour through a cut-off hour.
  2. Files that already failed keep `TranscodeDecisionMaker: "Transcode error"` and are
     never retried on their own. `bulk-update-files` puts them back to "Queued".

Everything is verified by re-reading counts, not by trusting the write.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request

KEY = "tapi_40c41a4b3245a315d16153d692a264f8"
BASE = "http://192.168.214.23:8265/api/v2"
LIBS = {"musicdocos": "d2mSD6V", "musicdocos-series": "pbfcK4h"}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def api(path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{BASE}/{path}", data=data,
        headers={"x-api-key": KEY, "Content-Type": "application/json"},
        method="POST" if data else "GET")
    body = urllib.request.urlopen(req, timeout=120).read().decode()
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body.strip()}


def crud(collection: str, mode: str, obj=None, doc_id=None):
    data = {"collection": collection, "mode": mode}
    if doc_id:
        data["docID"] = doc_id
    if obj is not None:
        data["obj"] = obj
    return api("cruddb", {"data": data})


def open_window(libs: dict[str, str], until_hour: int) -> None:
    """Tick every hour block from now until `until_hour` (inclusive-of-start)."""
    now = dt.datetime.now()
    day = DAYS[now.weekday()]
    settings = crud("LibrarySettingsJSONDB", "getAll")
    settings = settings if isinstance(settings, list) else list(settings.values())
    for lib in settings:
        if lib.get("name") not in libs:
            continue
        changed = []
        for block in lib.get("schedule") or []:
            bid = str(block.get("_id", ""))
            if not bid.startswith(day + ":"):
                continue
            start_hour = int(bid.split(":")[1].split("-")[0])
            if now.hour <= start_hour <= until_hour and not block.get("checked"):
                block["checked"] = True
                changed.append(bid)
        if changed:
            crud("LibrarySettingsJSONDB", "update", obj=lib, doc_id=lib.get("_id"))
        print(f"  {lib.get('name')}: opened {len(changed)} hour block(s) "
              f"{changed[:4]}{'...' if len(changed) > 4 else ''}")


def requeue_errors(libs: dict[str, str]) -> int:
    docs = crud("FileJSONDB", "getAll")
    docs = docs if isinstance(docs, list) else list(docs.values())
    # cruddb returns `DB` (uppercase) and uses the FILE PATH as `_id`; the SQL
    # table's lowercase `db` column is not what comes back over the API.
    stuck = [d for d in docs
             if d.get("DB") in libs.values()
             and str(d.get("TranscodeDecisionMaker")) == "Transcode error"]
    if not stuck:
        print("  no files in Transcode error state")
        return 0
    ids = [d.get("_id") for d in stuck if d.get("_id")]
    by_lib: dict[str, int] = {}
    for d in stuck:
        by_lib[d.get("DB")] = by_lib.get(d.get("DB"), 0) + 1
    print(f"  requeueing {len(ids)} file(s): {by_lib}")
    # Chunked: a single huge payload risks a request-body limit.
    for i in range(0, len(ids), 100):
        batch = ids[i:i + 100]
        api("bulk-update-files",
            {"data": {"fileIds": batch,
                      "updatedObj": {"TranscodeDecisionMaker": "Queued"}}})
        print(f"    sent {len(batch)}")
    return len(ids)


def counts(libs: dict[str, str]) -> dict:
    docs = crud("FileJSONDB", "getAll")
    docs = docs if isinstance(docs, list) else list(docs.values())
    out: dict[str, dict[str, int]] = {}
    for d in docs:
        db = d.get("DB")
        if db not in libs.values():
            continue
        name = next((n for n, v in libs.items() if v == db), db)
        status = str(d.get("TranscodeDecisionMaker"))
        out.setdefault(name, {})
        out[name][status] = out[name].get(status, 0) + 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until-hour", type=int, default=23,
                    help="open hour blocks from now up to this hour (default 23)")
    ap.add_argument("--requeue-errors", action="store_true")
    args = ap.parse_args()

    print("opening the schedule window...")
    open_window(LIBS, args.until_hour)
    if args.requeue_errors:
        print("requeueing failed files...")
        requeue_errors(LIBS)

    print("\nfile status now:")
    for lib, c in counts(LIBS).items():
        print(f"  {lib}: {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
