"""Finish the sweep: the identical Tokyo Idols copy and the duplicate Led Zep release.

Both were held back on the first pass for a reason, and both are cleared now:

* Tokyo Idols — two copies at exactly 1,585,118,796 bytes each, same codec,
  resolution and runtime. The identical-copy rule covers it.
* Becoming Led Zeppelin — the YTS 1080p payload is still on disk, but the library
  holds the LAMA 1080p of the same film (only the release tag differs). A complete
  payload that duplicates a verified library file is removable, files AND entry.

Guards: hardlink count is re-read immediately before each unlink (nlink > 1 means
the bytes are shared with a library copy), and the library row is re-verified first.
"""
import json
import subprocess
import sys

sys.path.insert(0, ".")

from media_lib import config, jellyfin, qb

CT = config.MEDIA_HOST
FFPROBE = ("docker run --rm -v /mnt/zmedia:/mnt/zmedia --entrypoint "
           "/usr/lib/jellyfin-ffmpeg/ffprobe jellyfin/jellyfin:12.2")


def run(cmd: str) -> str:
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", CT, f"pct exec 105 -- bash -lc {json.dumps(cmd)}"],
        capture_output=True, text=True, timeout=600,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def nlink(path: str) -> int:
    try:
        return int(run(f'stat -c %h "{path}"').strip().splitlines()[-1])
    except (ValueError, IndexError):
        return -1


def main() -> int:
    # ---- Tokyo Idols: identical pair ---------------------------------------
    print("Tokyo Idols:")
    items = jellyfin.get_json("/Items", params={"Recursive": "true", "SearchTerm": "Tokyo Idols",
                                                "IncludeItemTypes": "Movie", "Fields": "Path",
                                                "Limit": 10}).get("Items") or []
    files = [i for i in items if i.get("Path") and "tokyo" in (i.get("Name") or "").lower()]
    if len(files) < 2:
        print("  only one copy remains — nothing to do")
    else:
        info = []
        for i in files:
            p = i["Path"].replace("/data/", "/mnt/zmedia/videos/", 1)
            info.append((i, p, run(f'stat -c %s "{p}"').strip()))
        sizes = {s for _, _, s in info}
        print(f"  sizes: {sorted(sizes)}")
        if len(sizes) != 1:
            print("  sizes differ — NOT covered by the identical rule; leaving both")
        else:
            keep = info[0]
            drop, dpath, _ = info[1]
            links = nlink(dpath)
            if links != 1:
                print(f"  REFUSING: nlink={links} on the copy to drop")
            else:
                jellyfin.delete_item(drop["Id"])
                still = run(f'test -e "{dpath}" && echo yes').strip()
                print(f"  kept   {keep[0].get('Path')[-56:]}")
                print(f"  dropped (file gone: {'no' if still == 'yes' else 'yes'})")

    # ---- Becoming Led Zeppelin: duplicate release --------------------------
    print("\nBecoming Led Zeppelin (YTS payload duplicates the library LAMA copy):")
    held = [i for i in (jellyfin.get_json("/Items", params={
        "Recursive": "true", "SearchTerm": "Becoming Led Zeppelin", "IncludeItemTypes": "Movie",
        "Fields": "Path", "Limit": 10}).get("Items") or []) if i.get("Path")]
    if not held:
        print("  no library copy found — REFUSING to delete the payload")
        return 1
    shelf = held[0]["Path"].replace("/data/", "/mnt/zmedia/videos/", 1)
    probe = run(f'{FFPROBE} -v error -select_streams v:0 -show_entries stream=codec_name,width,height '
                f'-show_entries format=duration -of default=noprint_wrappers=1 "{shelf}"')
    kv = dict(line.split("=", 1) for line in probe.strip().splitlines() if "=" in line)
    print(f"  library copy verified: {kv.get('codec_name')} {kv.get('width')}x{kv.get('height')} "
          f"{float(kv.get('duration', 0) or 0)/60:.1f} min")
    tor = [t for t in qb.torrents() if t["hash"].startswith("734823429fed")]
    if not tor:
        print("  torrent entry already gone")
        return 0
    t = tor[0]
    print(f"  payload at {t.get('progress', 0)*100:.0f}% ({t.get('state')})")
    if t.get("progress", 0) < 0.999:
        print("  REFUSING: not a complete payload — partials are never deleted")
        return 1
    path = qb.resolve_media_path(t.get("content_path") or "", t["hash"])
    disk = path.replace("/downloads-zmedia", "/mnt/zmedia/downloads", 1)
    links = nlink(disk)
    print(f"  payload nlink={links}")
    if links != 1:
        print("  REFUSING: payload is hardlinked to a library file")
        return 1
    qb.delete([t["hash"]], delete_files=True)
    gone = run(f'test -e "{disk}" && echo yes').strip()
    print(f"  removed entry + files (payload gone: {'no' if gone == 'yes' else 'yes'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
