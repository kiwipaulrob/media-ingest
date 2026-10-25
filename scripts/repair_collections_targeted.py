#!/usr/bin/env python3
"""Targeted collection repair for the five that the generic pass could not match."""
import re
import sys
import time

sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import jellyfin  # noqa: E402


def flat(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def find(term, types="Movie,Series", limit="10"):
    r = jellyfin.get_json("/Items", params={"Recursive": "true", "SearchTerm": term,
                                             "IncludeItemTypes": types, "Limit": limit,
                                             "Fields": "Path"})
    return [i for i in (r.get("Items") or []) if i.get("Type") in ("Movie", "Series")]


def add(cid, ids, label):
    if not ids:
        print(f"  {label}: nothing to add")
        return
    try:
        jellyfin._request(f"/Collections/{cid}/Items?ids={','.join(ids)}", method="POST")
        time.sleep(2)
        r = jellyfin.get_json("/Items", params={"ParentId": cid, "Recursive": "true", "Limit": "1"})
        print(f"  {label}: added {len(ids)} -> {r.get('TotalRecordCount')}")
    except Exception as e:
        print(f"  {label}: FAILED {str(e)[:110]}")


bs = {b["Name"]: b["Id"] for b in
      ((jellyfin.get_json("/Items", params={"Recursive": "true", "IncludeItemTypes": "BoxSet",
                                            "Limit": "60"}) or {}).get("Items") or [])}

# 1. Design Trilogy — media is "Gary.Hustwits.Design.Trilogy", dots defeat word matching
if "Design Trilogy" in bs:
    hits = [i for i in find("Hustwit") if "designtrilogy" in flat(i.get("Name"))]
    add(bs["Design Trilogy"], [i["Id"] for i in hits], "Design Trilogy")

# 2. Louis Theroux & The Most Hated Family — media is "BBC.Louis.Theroux.Surviving.America's.Most..."
if "Louis Theroux & The Most Hated Family In America" in bs:
    hits = [i for i in find("Theroux", limit="20")
            if "mosthated" in flat(i.get("Name")) or "survivingamerica" in flat(i.get("Name"))]
    add(bs["Louis Theroux & The Most Hated Family In America"], [i["Id"] for i in hits], "Louis Theroux")

# 3. Harry Palmer — no item is titled "Harry Palmer"; the films are named individually
if "Harry Palmer Collection" in bs:
    want = ("ipcress", "funeralinberlin", "billiondollarbrain", "horsemen")
    hits = []
    for t in ("Ipcress", "Funeral in Berlin", "Billion Dollar Brain", "Horse Under Water"):
        hits += [i for i in find(t) if any(w in flat(i.get("Name")) for w in want)]
    seen, uniq = set(), []
    for i in hits:
        if i["Id"] not in seen:
            seen.add(i["Id"])
            uniq.append(i["Id"])
    add(bs["Harry Palmer Collection"], uniq, "Harry Palmer")

print("\n=== remaining empty (expected: Emmanuelle, Perry Mason) ===")
allb = (jellyfin.get_json("/Items", params={"Recursive": "true", "IncludeItemTypes": "BoxSet",
                                            "Limit": "60"}) or {}).get("Items") or []
for b in allb:
    r = jellyfin.get_json("/Items", params={"ParentId": b["Id"], "Recursive": "true", "Limit": "1"})
    if (r.get("TotalRecordCount") or 0) == 0:
        print("  empty:", b.get("Name"))
print("boxsets total:", len(allb))
