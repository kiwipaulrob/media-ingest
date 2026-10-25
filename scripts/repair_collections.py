#!/usr/bin/env python3
"""Repair boxsets whose membership was lost. Conservative: only add items whose normalised
title contains the collection's key term, and never add the boxset itself."""
import re
import sys
import time

sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import jellyfin  # noqa: E402

STOP = {"collection", "the", "a", "an", "of", "and", "&"}


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", str(s or "").lower()).strip()


def key_term(name):
    words = [w for w in norm(name).split() if w not in STOP]
    return " ".join(words)


def children(cid):
    try:
        r = jellyfin.get_json("/Items", params={"ParentId": cid, "Recursive": "true", "Limit": "1"})
        return r.get("TotalRecordCount") or 0
    except Exception:
        return -1


bs = jellyfin.get_json("/Items", params={"Recursive": "true", "IncludeItemTypes": "BoxSet",
                                          "Limit": "60"}) or {}
empties = []
for b in (bs.get("Items") or []):
    if children(b["Id"]) <= 0:
        empties.append(b)

print("empty boxsets:", len(empties))
for b in empties:
    term = key_term(b.get("Name"))
    if not term:
        print(f"  {b.get('Name')}: no usable key term, skipped")
        continue
    r = jellyfin.get_json("/Items", params={"Recursive": "true", "SearchTerm": term,
                                            "IncludeItemTypes": "Movie,Series",
                                            "Limit": "20", "Fields": "Path"})
    cands = []
    for i in (r.get("Items") or []):
        if i.get("Type") == "BoxSet" or i.get("Id") == b.get("Id"):
            continue
        if term in norm(i.get("Name")):
            cands.append(i)
    print(f"\n  {b.get('Name')!r}  term={term!r}  candidates={len(cands)}")
    for c in cands[:6]:
        print(f"      {c.get('Type'):<7} {str(c.get('Name'))[:44]}")
    if not cands:
        continue
    ids = ",".join(c["Id"] for c in cands)
    try:
        jellyfin._request(f"/Collections/{b['Id']}/Items?ids={ids}", method="POST")
        time.sleep(2)
        print(f"      added {len(cands)} -> children now {children(b['Id'])}")
    except Exception as e:
        print(f"      add failed: {str(e)[:110]}")

print("\n=== final ===")
bs = jellyfin.get_json("/Items", params={"Recursive": "true", "IncludeItemTypes": "BoxSet",
                                          "Limit": "60"}) or {}
still = [b.get("Name") for b in (bs.get("Items") or []) if children(b["Id"]) <= 0]
print("boxsets:", len(bs.get("Items") or []), "| still empty:", len(still), still)
