"""Which downloaded files are for episodes the library does NOT already hold?

Basenames are useless for this: placement renames files, so a download called
`Classic.Albums.S03E03.Metallica.Black.Album.PDTV.x264-LiNKLE.mkv` is held as
`Classic Albums (1997) S03E03 Metallica - The Black Album.mkv`. Comparing names
reported every single file as "not held".

So compare by TITLE IDENTITY (shared distinctive tokens) against both the episode
rows and the standalone movie entries, and only report files whose best match is
weak — those are the episodes genuinely missing with a file already on disk.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, ".")
from media_lib import jellyfin, rowcheck  # noqa: E402

DOWNLOADS = "/mnt/zmedia/downloads"
RELEASE_NOISE = re.compile(
    r"\b(720p|1080p|1080i|x264|x265|h264|hevc|aac|ac3|eac3|dts|webrip|web|hdtv|pdtv|"
    r"dvdrip|xvid|mvgroup|eztv|yts|rarbg|hulu|linkle|forum|org|proper|repack|"
    r"1of2|2of2|s\d+e\d+)\b", re.I)


def identity(filename: str) -> str:
    """Strip release cruft so the title can be compared with a library row."""
    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    stem = stem.replace(".", " ").replace("_", " ").replace("-", " ")
    stem = RELEASE_NOISE.sub(" ", stem)
    return re.sub(r"\s+", " ", stem).strip()


def ssh(cmd: str, timeout: int = 300) -> str:
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=12", "root@192.168.214.109",
         f"pct exec 105 -- bash -lc {json.dumps(cmd)}"],
        capture_output=True, text=True, timeout=timeout,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def library_titles(uid: str):
    """(name, tokens) for everything the music-doco libraries hold."""
    out = []
    views = jellyfin.get_json(f"/Users/{uid}/Views").get("Items") or []
    for v in views:
        if v.get("Name") not in ("Music Docos Series", "Music Docos"):
            continue
        for s in jellyfin.get_json(f"/Users/{uid}/Items", params={
                "ParentId": v["Id"], "Recursive": "true",
                "IncludeItemTypes": "Series", "Limit": 500}).get("Items") or []:
            eps = jellyfin.get_json(f"/Shows/{s['Id']}/Episodes", params={
                "UserId": uid, "Fields": "Path", "Limit": 500}).get("Items") or []
            for e in eps:
                if e.get("Path"):
                    out.append((e.get("Name") or "", rowcheck.tokens(e.get("Name"))))
                    out.append((e["Path"].rsplit("/", 1)[-1],
                                rowcheck.tokens(identity(e["Path"]))))
        for m in jellyfin.get_json(f"/Users/{uid}/Items", params={
                "ParentId": v["Id"], "Recursive": "true",
                "IncludeItemTypes": "Movie,Video", "Limit": 1000}).get("Items") or []:
            out.append((m.get("Name") or "", rowcheck.tokens(m.get("Name"))))
    return out


def main() -> int:
    uid = jellyfin.user_id()
    held = library_titles(uid)
    print(f"library titles indexed for comparison: {len(held)}")

    raw = ssh(f'find "{DOWNLOADS}" -maxdepth 3 -type f 2>/dev/null '
              f"| grep -iE '\\.(mkv|mp4|avi|m4v|ts|mov)$'")
    files = [p for p in raw.strip().splitlines() if p.strip()]
    print(f"media files in downloads: {len(files)}\n")

    missing, already = [], []
    for path in files:
        want = rowcheck.tokens(identity(path))
        best, score = "", 0.0
        for name, toks in held:
            if not toks:
                continue
            overlap = len(want.intersection(toks)) / max(1, len(want))
            if overlap > score:
                best, score = name, overlap
        rec = {"file": path.rsplit("/", 1)[-1], "path": path,
               "identity": identity(path), "best_library_match": best,
               "score": round(score, 2)}
        (already if score >= 0.6 else missing).append(rec)

    print(f"=== NOT held by the library ({len(missing)}) — placeable or huntable ===")
    for r in sorted(missing, key=lambda r: -r["score"]):
        print(f"  [{r['score']:.2f}] {r['identity'][:52]:<53} (closest: {r['best_library_match'][:26]})")
    print(f"\n=== already held ({len(already)}) — source files still seeding ===")
    for r in sorted(already, key=lambda r: -r["score"]):
        print(f"  [{r['score']:.2f}] {r['file'][:66]}")

    out = Path(__file__).resolve().parents[1] / "state" / "downloads-not-held.json"
    out.write_text(json.dumps({"not_held": missing, "already_held": already}, indent=1))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
