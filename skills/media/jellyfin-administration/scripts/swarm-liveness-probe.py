#!/usr/bin/env python3
"""Classify candidate torrents as LIVE or DEAD on the local qBittorrent.

Usage:
    swarm-liveness-probe.py [--wait SECONDS] <infohash|magnet> [more ...]

Env: QBITTORRENT_URL (default http://192.168.214.11:8090)

SIDE EFFECT: ADDS each candidate to qBittorrent (tagged swarm-probe, force-started) so it
can look for peers. Nothing is ever deleted. Clean up the DEAD ones by hand afterwards.

Why this exists: public index seeder counts are stale and routinely claim seeds for empty
swarms. Adding the magnet and watching is the only trustworthy liveness test.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

QB = os.environ.get("QBITTORRENT_URL", "http://192.168.214.11:8090")
TAG = "swarm-probe"
DEAD_STATES = ("metaDL", "forcedMetaDL")


def post(path, params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(QB + path, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode().strip()
    except urllib.error.HTTPError as e:
        # 409 = duplicate by FILE identity, not infohash. Check the log to confirm.
        return f"HTTP {e.code}: {e.read().decode()[:200]}"


def get(path):
    with urllib.request.urlopen(QB + path, timeout=30) as r:
        return json.load(r)


def infohash(arg):
    m = re.search(r"btih:([0-9a-fA-F]{40}|[A-Za-z2-7]{32})", arg)
    if m:
        return m.group(1).lower()
    if re.fullmatch(r"[0-9a-fA-F]{40}", arg):
        return arg.lower()
    return None


def main():
    args = sys.argv[1:]
    wait = 240
    if "--wait" in args:
        i = args.index("--wait")
        wait = int(args[i + 1])
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 2

    cands = {}
    for a in args:
        h = infohash(a)
        if h:
            cands[h] = a
        else:
            print(f"skip (no infohash): {a[:70]}")
    if not cands:
        print("nothing to probe")
        return 2

    before = {t["hash"] for t in get("/api/v2/torrents/info")}
    urls = "\n".join(
        c if c.startswith("magnet:") else f"magnet:?xt=urn:btih:{c}"
        for c in cands.values()
    )
    print("add:", post("/api/v2/torrents/add", {"urls": urls, "tags": TAG}))

    time.sleep(5)
    fresh = {t["hash"] for t in get("/api/v2/torrents/info")} - before
    if fresh:
        # bypass the max_active_downloads cap, which parks new adds in queuedDL
        post("/api/v2/torrents/setForceStart",
             {"hashes": "|".join(sorted(fresh)), "value": "true"})

    print(f"waiting {wait}s for metadata/peers ...")
    time.sleep(wait)

    info = {t["hash"]: t for t in get("/api/v2/torrents/info")}
    live = dead = 0
    print(f'\n{"verdict":<10}{"state":<14}{"prog":>7}{"seeds":>7}{"KB/s":>9}  name')
    for h, src in cands.items():
        t = info.get(h)
        if not t:
            print(f'{"REJECTED":<10}{"-":<14}{"-":>7}{"-":>7}{"-":>9}  {src[:52]}')
            continue
        ok = t["state"] not in DEAD_STATES and (
            t["progress"] > 0 or t["num_seeds"] > 0 or t["dlspeed"] > 0
        )
        live += ok
        dead += (not ok)
        print(f'{"LIVE" if ok else "DEAD":<10}{t["state"]:<14}{t["progress"] * 100:6.1f}%'
              f'{t["num_seeds"]:7}{t["dlspeed"] / 1024:9.1f}  {t["name"][:52]}')

    print(f"\nLIVE {live} / {len(cands)}  — a low yield is normal; index seed counts are stale")
    if dead:
        print("DEAD = still forcedMetaDL with 0 peers. Do not retry or recheck; find another source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
