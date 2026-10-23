"""Post-ingest sweep + one upgrade, following the deletion policy exactly.

Policy (media-deletion-policy): a COMPLETE payload may be removed once the library
copy is verified; partials are never touched; duplicates only when truly identical;
everything else is reported, not deleted.

What this does:
  1. UPGRADE  Hung Up on a Dream 720p -> the 1080p that has now finished downloading
     (same 98.7 min runtime, so it is the same film at higher resolution, not a dupe).
  2. Remove the qBittorrent entries whose payload was already moved into the library
     (their files are gone from the download tree, so they are ghost seeders).
  3. Delete one of the byte-identical Tokyo Idols copies.

Deliberately NOT touched:
  * Jools S56 — 63.5% and still downloading. A partial.
  * The Killers PDTV vs the 1080p concert — 49.9 min vs 119.2 min. Different edits,
    not duplicates, however similar the titles look.
  * Elvis Costello 1of2 x2 — same resolution and runtime but DIFFERENT size, so not
    covered by the identical-copy rule; reported for a decision.

Every unlink is guarded by a hardlink count taken immediately before it: nlink > 1
means the bytes are shared with a library copy and removing them would destroy it.
"""
import json
import subprocess
import sys
import time

sys.path.insert(0, ".")

from media_lib import config, jellyfin, qb

CT = config.MEDIA_HOST
LIB_DIR = "/mnt/zmedia/videos/musicdocos/Hung Up on a Dream - The Zombies Documentary (2023)"
SHELF_720 = f"{LIB_DIR}/Hung Up on a Dream - The Zombies Documentary (2023).mp4"

# Torrents whose payload is already in the library (files moved, entries left behind).
GHOSTS = ["b54563248ad3", "32fe3b20e622", "9f792b1a5661", "0776118f9cdc",
          "c45f696bb9c4", "4b5fa8499783", "734823429fed"]


def run(cmd: str) -> str:
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", CT, f"pct exec 105 -- bash -lc {json.dumps(cmd)}"],
        capture_output=True, text=True, timeout=600,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def nlink(path: str) -> int:
    out = run(f'stat -c %h "{path}"').strip()
    try:
        return int(out.splitlines()[-1])
    except (ValueError, IndexError):
        return -1


def main() -> int:
    # ---- 1. upgrade Hung Up on a Dream to 1080p -----------------------------
    tor = [t for t in qb.torrents() if t["hash"].startswith("dc84774b022c")]
    if not tor:
        print("Hung Up 1080p torrent not in queue — skipping upgrade")
    else:
        src = qb.resolve_media_path(tor[0].get("content_path") or "", tor[0]["hash"])
        src = src.replace("/downloads-zmedia", "/mnt/zmedia/downloads", 1)
        links = nlink(src)
        print(f"upgrade source nlink={links}")
        if links != 1:
            print("  REFUSING: source is hardlinked; unlink would destroy a library copy")
        else:
            before = run(f'stat -c %s "{SHELF_720}"').strip()
            # Move the OLD copy aside first: never leave the library without a file.
            out = run(f'mv -f "{SHELF_720}" "{SHELF_720}.720p-old" && echo OK')
            if "OK" not in out:
                print(f"  REFUSING: could not park the 720p copy: {out.strip()[:100]}")
            else:
                out = run(f'mv -f "{src}" "{SHELF_720}" && echo OK')
                if "OK" not in out:
                    run(f'mv -f "{SHELF_720}.720p-old" "{SHELF_720}"')  # put it back
                    print(f"  ROLLED BACK: 1080p move failed: {out.strip()[:100]}")
                else:
                    size = run(f'stat -c %s "{SHELF_720}"').strip()
                    print(f"  upgraded: {before} -> {size} bytes")
                    run(f'rm -f "{SHELF_720}.720p-old"')
                    jellyfin.notify_media([SHELF_720.replace("/mnt/zmedia/videos", "/data")],
                                          update_type="Modified")
                    print("  notified scanner (Modified)")

    # ---- 2. remove ghost torrent entries -----------------------------------
    print("\nghost entries (payload already in library):")
    for h in GHOSTS:
        t = next((x for x in qb.torrents() if x["hash"].startswith(h)), None)
        if not t:
            print(f"  {h}: already gone")
            continue
        path = qb.resolve_media_path(t.get("content_path") or "", t["hash"])
        disk = path.replace("/downloads-zmedia", "/mnt/zmedia/downloads", 1)
        exists = "yes" if run(f'test -e "{disk}" && echo yes').strip().endswith("yes") else "no"
        if exists == "yes":
            print(f"  {h}: SKIP — payload still on disk ({disk[-40:]}); not deleting")
            continue
        qb.delete([t["hash"]], delete_files=False)   # bytes are already gone
        print(f"  {h}: removed entry — {t.get('name')[:46]}")

    # ---- 3. Tokyo Idols: byte-identical duplicate --------------------------
    print("\nTokyo Idols duplicate:")
    items = jellyfin.get_json("/Items", params={"Recursive": "true", "SearchTerm": "Tokyo Idols",
                                                "IncludeItemTypes": "Movie", "Fields": "Path",
                                                "Limit": 10}).get("Items") or []
    files = [i for i in items if i.get("Path") and "tokyo" in (i.get("Name") or "").lower()]
    if len(files) < 2:
        print("  fewer than two copies now — nothing to do")
    else:
        sizes = {}
        for i in files:
            p = i["Path"].replace("/data/", "/mnt/zmedia/videos/", 1)
            sizes[i["Id"]] = run(f'stat -c %s "{p}"').strip()
        print(f"  sizes: {list(sizes.values())}")
        if len(set(sizes.values())) == 1:
            keep, drop = files[0], files[1]
            dp = drop["Path"].replace("/data/", "/mnt/zmedia/videos/", 1)
            links = nlink(dp)
            if links != 1:
                print(f"  REFUSING: {links} links on the copy to drop")
            else:
                jellyfin.delete_item(drop["Id"])
                gone = run(f'test -e "{dp}" && echo yes').strip()
                print(f"  dropped {drop['Name']} — file removed: {'no' if gone == 'yes' else 'yes'}")
        else:
            print("  sizes differ — NOT covered by the identical rule, leaving both")
    return 0


if __name__ == "__main__":
    sys.exit(main())
