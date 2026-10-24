#!/usr/bin/env python3
"""Triage SAB failures: retry only what is worth retrying, and find replacements.

Why this exists (24 Oct 2026): 32 failed downloads were retried after a block account
was topped up. Every one failed identically, because "not enough repair blocks (N short)"
does not mean "no credit" — it means the articles are absent from EVERY configured
provider. Credit cannot buy articles the backbone does not have.

The useful split is the size of the shortfall:

    <= 10 blocks   retry        a second provider often fills a near-complete post
    11-100         maybe        usually dead; worth one attempt, then replace
    > 100          dead-post    hopeless; replace the release, never retry
    corrupt RAR / bad archive  damaged-post — replace
    not on your server / aborted  missing-from-provider — replace

    usenet_triage.py                 # classify everything in history
    usenet_triage.py --retry-small   # requeue only the <=10-block group
    usenet_triage.py --search        # for the hopeless ones, list alternative releases
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from media_lib import quality, sab  # noqa: E402

_STOP = {"1080p", "720p", "480p", "2160p", "hdtv", "webrip", "web", "webdl", "bluray",
         "x264", "x265", "hevc", "h264", "aac", "ac3", "ddp", "dd5", "10bit", "proper",
         "internal", "repack", "yts", "mvgroup", "megusta", "lama", "bae", "kitsune",
         "eztv", "rarbg", "amzn", "nf", "dsnp"}


def clean_title(name: str) -> str:
    """Release name -> searchable title."""
    t = re.sub(r"[._]+", " ", str(name))
    t = re.sub(r"\[[^\]]*\]", " ", t)
    words = [w for w in t.split() if w.lower().strip("-") not in _STOP]
    return " ".join(words).strip()[:90]


def prowlarr_search(term: str, limit: int = 40) -> List[Dict[str, Any]]:
    """Ask Prowlarr for releases matching `term`. Returns [] on any failure."""
    base = os.environ.get("PROWLARR_URL", "").rstrip("/")
    key = os.environ.get("PROWLARR_API_KEY", "")
    if not base or not key or not term:
        return []
    url = f"{base}/api/v1/search?query={urllib.parse.quote(term)}&type=search&limit={limit}"
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(url, headers={"X-Api-Key": key}), timeout=240).read()
        return json.loads(raw)
    except Exception:
        return []



def _queue(result: Dict[str, Any]) -> bool:
    """Add one Prowlarr result to qBittorrent (magnet preferred) or SABnzbd."""
    from media_lib import qb
    url = result.get("downloadUrl") or ""
    if result.get("protocol") == "usenet":
        from media_lib import sab
        try:
            return bool(sab.add_url(url, name=str(result.get("title", "")), category="movies"))
        except Exception:
            return False
    if not url:
        return False
    try:
        res = qb.add_release(result, category="musicdocos")
        return bool(res["ok"] and res["verified"])
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retry-small", action="store_true",
                    help="requeue only failures <=10 blocks short")
    ap.add_argument("--search", action="store_true",
                    help="search Prowlarr for replacements of hopeless failures")
    ap.add_argument("--queue-upgrades", action="store_true",
                    help="with --search, queue only releases PROVEN better than what we hold")
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()

    hist = sab.history(limit=args.limit) or []
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for j in hist:
        if str(j.get("status")).lower() != "failed":
            continue
        c = sab.classify_failure(j.get("fail_message", ""), j)
        rec = dict(j)
        rec["class"] = c
        buckets.setdefault(c["kind"], []).append(rec)

    order = ["retry", "maybe", "dead-post", "post-incomplete-at-provider",
             "damaged-post", "missing-from-provider", "other"]
    total = sum(len(v) for v in buckets.values())
    print(f"{total} failed job(s) in the last {args.limit} history entries\n")
    for kind in order:
        jobs = buckets.get(kind) or []
        if not jobs:
            continue
        print(f"--- {kind.upper()} ({len(jobs)})")
        for j in jobs:
            short = j["class"]["blocks_short"]
            tag = f"{short:>5} short" if short is not None else "  -  "
            print(f"   {tag}  {str(j.get('name'))[:64]}")
        print()

    if args.retry_small:
        small = buckets.get("retry") or []
        print(f"retrying {len(small)} near-complete job(s) (<= {sab.REPAIR_SMALL} blocks)")
        for j in small:
            try:
                ok = sab.retry(j["nzo_id"])
            except Exception:
                ok = False
            print(f"   {'queued' if ok else 'refused'}  {str(j.get('name'))[:60]}")
        if small:
            print("   NOTE: if the queue is paused the retries will sit idle — "
                  "resume it with sab._call('resume')")

    if args.search:
        hopeless = (buckets.get("dead-post") or []) + (buckets.get("damaged-post") or []) \
                   + (buckets.get("missing-from-provider") or [])
        print(f"\nsearching replacements for {len(hopeless)} hopeless job(s)\n")
        for j in hopeless:
            title = clean_title(j.get("name", ""))
            results = prowlarr_search(title)
            keep = [r for r in results if title.split()[0].lower() in str(r.get("title", "")).lower()]
            keep.sort(key=lambda r: -(r.get("seeders") or 0))
            print(f"  {title[:58]}")
            if not keep:
                print("      no alternative found")
                continue
            queued = False
            for r in keep[:3]:
                size = int(r.get("size") or 0) / 1e9
                # NEVER queue a replacement on seeds alone: it must beat what we hold.
                v = quality.decide(str(r.get("title", "")), title)
                flag = {"upgrade": "QUEUE", "no-local-copy": "QUEUE(GAP)",
                        "same-or-worse": "skip", "unknown": "skip"}[v["verdict"]]
                print(f"      [{flag:<9}] {r.get('protocol','?'):<7} {size:5.2f}GB "
                      f"seeds={str(r.get('seeders','-')):<4} {str(r.get('title'))[:50]}")
                print(f"                  {v['reason']}")
                if args.queue_upgrades and v["queue"] and not queued:
                    queued = _queue(r)
                    print(f"                  -> {'queued' if queued else 'queue FAILED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
