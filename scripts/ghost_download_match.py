"""Match ghost rows against media already sitting in the download folder.

A ghost row means "no file in the library" — but the file may already be on disk in
`/downloads-zmedia`, grabbed and never placed. That is the cheapest kind of missing
episode: it needs a placement, not a hunt.

Compares normalised titles both ways (release naming vs library naming) and reports
the best candidate per ghost.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, ".")
from media_lib import jellyfin  # noqa: E402

DOWNLOADS = "/mnt/zmedia/downloads"
VIDEO = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov")
STOP = {"the", "and", "of", "a", "in", "on", "classic", "albums", "album",
        "bonus", "material", "part", "1of2", "2of2", "x264", "ac3", "mvgroup"}


def ssh(cmd: str, timeout: int = 300) -> str:
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=12", "root@192.168.214.109",
         f"pct exec 105 -- bash -lc {json.dumps(cmd)}"],
        capture_output=True, text=True, timeout=timeout,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def toks(text: str) -> set:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower().replace("&", " and "))
    return {t for t in cleaned.split() if len(t) >= 3 and t not in STOP}


def main() -> int:
    inv = json.loads((Path(__file__).resolve().parents[1] / "state"
                      / "musicdoco-inventory.json").read_text())
    ghosts = []
    for lib in inv["libraries"]:
        if lib["library"] != "Music Docos Series":
            continue
        for s in lib["series"]:
            for name in s.get("ghost_names") or []:
                if name and name.strip():
                    ghosts.append((s["name"], name.strip()))

    raw = ssh(f'find "{DOWNLOADS}" -maxdepth 3 -type f 2>/dev/null '
              f"| grep -iE '\\.(mkv|mp4|avi|m4v|ts|mov)$'")
    files = [p for p in raw.strip().splitlines() if p.strip()]
    print(f"ghost rows: {len(ghosts)}   media files in downloads: {len(files)}\n")

    hits = []
    for series, name in ghosts:
        want = toks(name)
        if not want:
            continue
        best, score, path = None, 0.0, ""
        for path in files:
            fname = path.rsplit("/", 1)[-1]
            have = toks(fname)
            if not have:
                continue
            overlap = len(want.intersection(have)) / len(want)
            if overlap > score:
                best, score = path, overlap
        if best and score >= 0.6:
            hits.append({"series": series, "ghost": name, "file": best.rsplit("/", 1)[-1],
                         "path": path, "score": round(score, 2)})

    hits.sort(key=lambda h: -h["score"])
    print(f"ghosts with a likely file already downloaded: {len(hits)}\n")
    seen = set()
    for h in hits:
        key = h["file"]
        if key in seen:
            continue
        seen.add(key)
        print(f"  [{h['score']:.2f}] {h['ghost'][:44]:<45} <- {h['file'][:64]}")
    out = Path(__file__).resolve().parents[1] / "state" / "ghost-download-matches.json"
    out.write_text(json.dumps(hits, indent=1))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
