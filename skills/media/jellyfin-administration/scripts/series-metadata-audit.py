#!/usr/bin/env python3
"""Per-series episode metadata coverage for a Shows library.

Reports, per series: episode count, how many episodes lack an overview, an air
date, or an image. Answers "where is a metadata repair pass worth running?" and
catches the timing artifact where a freshly-scanned series reads empty.

Usage:
    series-metadata-audit.py [--library <name-or-id>] [--user <id>]

Defaults to the Shows-type library (collectionType tvshows) and the first admin
user found. Credentials come from /root/.hermes/.env (JELLYFIN_URL,
JELLYFIN_API_KEY). The item query MUST name every field it asserts on -- a
summary computed without Overview in Fields reports 0 overviews and looks like
a data defect.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

ENV = "/root/.hermes/.env"


def env(key):
    m = re.search(r"^%s=(\S+)" % key, open(ENV).read(), re.M)
    if not m:
        sys.exit("missing %s in %s" % (key, ENV))
    return m.group(1).strip().strip('"').strip("'")


J = env("JELLYFIN_URL").rstrip("/")
AUTH = {"Authorization": 'MediaBrowser Token="%s"' % env("JELLYFIN_API_KEY")}


def get(path, tries=4):
    """GET with retries -- the server answers empty mid-scan, which is not data."""
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(J + path, headers=AUTH)
            return json.load(urllib.request.urlopen(req, timeout=90))
        except Exception as e:            # noqa: BLE001 - retry any transport hiccup
            last = e
            time.sleep(4)
    print("  ! query failed after %d tries: %s" % (tries, last), file=sys.stderr)
    return {}


def pick_library(name_or_id):
    folders = get("/Library/VirtualFolders")
    if isinstance(folders, list):
        for f in folders:
            if name_or_id and (f.get("Name") == name_or_id
                               or f.get("ItemId") == name_or_id):
                return f["ItemId"], f.get("Name")
        for f in folders:
            if f.get("CollectionType") == "tvshows":
                return f["ItemId"], f.get("Name")
    sys.exit("no matching library (pass --library with a library name or id)")


def pick_user(user_id):
    if user_id:
        return user_id
    users = get("/Users")
    for u in users if isinstance(users, list) else []:
        if (u.get("Policy") or {}).get("IsAdministrator"):
            return u["Id"]
    sys.exit("no admin user found (pass --user)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", default=None,
                    help="library name or item id (default: the tvshows library)")
    ap.add_argument("--user", default=None, help="user id for scoped reads")
    args = ap.parse_args()

    lib, libname = pick_library(args.library)
    uid = pick_user(args.user)
    print("library: %s (%s)  user: %s" % (libname, lib, uid[:12]))

    series = get("/Users/%s/Items?ParentId=%s&Recursive=true"
                 "&IncludeItemTypes=Series&Fields=ChildCount&Limit=500" % (uid, lib))
    items = series.get("Items", [])
    print("series: %d\n" % len(items))

    gaps = 0
    for s in sorted(items, key=lambda x: x.get("Name") or ""):
        if not s.get("ChildCount"):
            print("%-44s NO SEASONS (empty shell)" % (s.get("Name") or "")[:44])
            continue
        eps = get("/Users/%s/Items?ParentId=%s&Recursive=true&IncludeItemTypes=Episode"
                  "&Fields=Overview,PremiereDate,ImageTags&Limit=500"
                  % (uid, s["Id"])).get("Items", [])
        if not eps:
            print("%-44s eps=0 (metadata not back yet?)" % (s.get("Name") or "")[:44])
            continue
        nov = sum(1 for e in eps if not e.get("Overview"))
        nod = sum(1 for e in eps if not e.get("PremiereDate"))
        noi = sum(1 for e in eps if not (e.get("ImageTags") or {}).get("Primary"))
        flag = ""
        if nov or nod:
            gaps += 1
            flag = "  <-- GAPS"
        print("%-44s eps=%-3d no-overview=%-3d no-date=%-3d no-image=%-3d%s"
              % ((s.get("Name") or "")[:44], len(eps), nov, nod, noi, flag))

    print("\nseries with overview/date gaps: %d of %d" % (gaps, len(items)))
    print("Re-read a gap on the first episode before acting -- provider metadata "
          "lands a few minutes after indexing.")


if __name__ == "__main__":
    main()
