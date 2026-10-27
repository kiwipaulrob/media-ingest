"""Wanted-driven hunts (the clawarr `wanted list` idea, approval-gated).

`state/wanted.json` is the watchlist: gaps with title keys and optional
episode. This script hunts every entry via hunt.plan and prints candidates.
READ-ONLY — it never queues. Queuing stays an explicit human pick via
hunt.queue (or the Telegram yes).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, ".")

from media_lib import config, hunt  # noqa: E402


def load_wanted(path: str) -> list:
    """Validate the watchlist schema. Pure."""
    with open(path) as f:
        data = json.load(f)
    entries = data.get("wanted", data) if isinstance(data, dict) else data
    out = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("query") or not e.get("keys"):
            raise ValueError(f"bad wanted entry (needs query + keys): {e!r}")
        out.append(
            {
                "query": e["query"],
                "keys": e["keys"],
                "protocol": e.get("protocol"),
                "episode": tuple(e["episode"]) if e.get("episode") else None,
                "note": e.get("note", ""),
            }
        )
    return out


def search(query: str) -> list:
    base = config.prowlarr_url().rstrip("/")
    url = f"{base}/api/v1/search?{urllib.parse.urlencode({'query': query, 'type': 'search'})}"
    req = urllib.request.Request(url, headers={"X-Api-Key": config.prowlarr_key()})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Hunt the watchlist (read-only).")
    ap.add_argument("--wanted", default="state/wanted.json")
    a = ap.parse_args(argv)
    for e in load_wanted(a.wanted):
        try:
            res = search(e["query"])
        except Exception as exc:  # noqa: BLE001 - one gap must not kill the sweep
            print(f"== {e['query']}: {type(exc).__name__}")
            continue
        cands = hunt.plan(res, keys=e["keys"], protocol=e["protocol"], episode=e["episode"])
        print(f"== {e['query']} ({e['note']}): {len(cands)} candidates")
        for x in cands[:4]:
            print(
                f"   seed={x.get('seeders')} proto={x.get('protocol')} "
                f"{round((x.get('size') or 0) / 1e6)}MB {str(x.get('title'))[:62]}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
