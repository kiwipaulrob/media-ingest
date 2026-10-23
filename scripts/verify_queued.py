"""Find just-queued torrents by TITLE and force-start them.

Two traps this handles, both hit on the first attempt:

  * a Prowlarr result may carry no infohash (no magnet/guid hash), so verifying by
    hash reports "not in the queue" for a torrent that was in fact added;
  * this *arr pipeline runs 93 downloads against a cap of 25, so an added torrent
    sits in `queuedDL` and never starts unless it is force-started.

Verification is by normalized title, which is what a human checks anyway.
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, ".")

QB = "http://192.168.214.11:8090"


# NOTE: this predates media_lib.qb and talks to qBittorrent with its own
# qb_get/qb_post helpers below. It does NOT share source.queue()'s implementation,
# so a change to queue semantics must be made in both places. Consolidating onto
# media_lib.qb is the intended cleanup; not done while a batch was running.


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def qb_get(path: str):
    with urllib.request.urlopen(f"{QB}/api/v2/{path}", timeout=60) as fh:
        return json.loads(fh.read() or b"[]")


def qb_post(path: str, data: dict):
    req = urllib.request.Request(
        f"{QB}/api/v2/{path}", data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as fh:
        return fh.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", nargs="+", required=True,
                    help="normalized title fragments to find and force-start")
    ap.add_argument("--no-force", action="store_true")
    args = ap.parse_args()

    torrents = qb_get("torrents/info")
    found, missing = [], []
    for frag in args.match:
        want = norm(frag)
        hits = [t for t in torrents if want in norm(t["name"])]
        if not hits:
            missing.append(frag)
            continue
        for t in hits:
            found.append((frag, t))

    print(f"queue: {len(torrents)} torrents")
    for frag, t in found:
        print(f"  FOUND  {t['hash'][:12]} {t['progress']*100:5.1f}% "
              f"{t['state']:<12} {t['size']/1e9:6.2f}GB  {t['name'][:52]}")
    for frag in missing:
        print(f"  ABSENT {frag}")

    to_force = [t["hash"] for _, t in found if t["state"] in ("queuedDL", "stalledDL", "metaDL")]
    if to_force and not args.no_force:
        qb_post("torrents/setForceStart",
                {"hashes": "|".join(to_force), "value": "true"})
        print(f"\nforce-started {len(to_force)} torrent(s)")
        time.sleep(8)
        after = {t["hash"]: t for t in qb_get("torrents/info")}
        print("after force-start:")
        for h in to_force:
            t = after.get(h)
            if t:
                print(f"  {h[:12]} {t['progress']*100:5.1f}% {t['state']:<12} "
                      f"{t['name'][:52]}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
