"""Dupe sweep: video files in downloads + active torrent names vs full library.

Excludes hardlinked files (nlink > 1, i.e. shared-inode copies) from dupe
flagging and reports them separately.
"""
import json
import re
import sys

sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import jellyfin, probe, qb, rowcheck  # noqa: E402

STOP = {"the", "a", "an", "of", "and", "in", "on", "at", "to", "for",
        "s01", "s1", "1of2", "2of2", "3of3", "1of3", "2of3", "4of4", "1of4"}


def identity(name: str) -> str:
    stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    stem = re.sub(r"[._\-]+", " ", stem)
    stem = re.sub(r"\b(720p|1080p|1080i|2160p|4k|x264|x265|h264|h265|hevc|"
                  r"aac|ac3|eac3|dts|mp3|webrip|web|hdtv|pdtv|dvdrip|bluray|"
                  r"blu-ray|xvid|divx|mvgroup|org|forum|proper|repack|remux|"
                  r"dvd|ntsc|pal|avi|mkv|mp4|ts|m2ts|rarbg|eztv|yts|cbfm|"
                  r"miSERABLE|lithium|rartv|tastetv|rofl|rartv|galaxytv|"
                  r"cadaver|ethd|mctv|darkflix|megusta|btn|nx|cbfm|wnA|oft|"
                  r"kuchu|mubi|liquid|rarbg|cadaver|tastetv)\b", " ", stem,
                  flags=re.I)
    stem = re.sub(r"\b(s\d+e\d+|e\d+|\d+of\d+)\b", " ", stem, flags=re.I)
    toks = {t.lower() for t in re.split(r"\s+", stem) if t and len(t) > 1}
    return " ".join(sorted(toks - STOP))


def main() -> int:
    print("== download video files (nlink filter) ==")
    rc, out = probe.query(
        "find /mnt/zmedia/downloads/ -type f "
        r"\( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.avi' "
        r"-o -iname '*.ts' -o -iname '*.m2ts' -o -iname '*.wmv' \) "
        "-printf '%n|%s|%p\\n' 2>/dev/null | sort -t '|' -k 2 -n -r")
    cand, shared = [], []
    for line in out.strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        nlink, size, path = int(parts[0]), int(parts[1]), parts[2]
        if nlink > 1:
            shared.append((path, size))
        else:
            cand.append((path, size))
    print(f"video files: {len(cand)} single-link, {len(shared)} hardlinked (excluded)")
    for p, s in shared:
        print(f"  shared nlink>1: {p[-70:]} ({s/1e9:.2f} GB)")

    print("\n== library index ==")
    uid = jellyfin.user_id()
    views = jellyfin.get_json(f"/Users/{uid}/Views").get("Items") or []
    held = []  # (label, identity-tokens)
    for v in views:
        if v.get("CollectionType") not in ("tvshows", "movies", "musicvideos"):
            continue
        page = jellyfin.get_json(f"/Users/{uid}/Items", params={
            "ParentId": v["Id"], "Recursive": "true",
            "IncludeItemTypes": "Movie,Series,Episode",
            "Fields": "Name,Path,ProductionYear", "Limit": 20000}).get("Items") or []
        for it in page:
            nm = it.get("Name") or ""
            toks = set(rowcheck.tokens(nm)) - STOP
            held.append((f"{it.get('Type')}:{nm}", toks))
            pth = it.get("Path") or ""
            if pth:
                held.append((f"path:{pth.rsplit('/',1)[-1]}",
                             set(rowcheck.tokens(identity(pth))) - STOP))
    print(f"library rows indexed: {len(held)}")

    def verdict(name):
        want = set(identity(name).split())
        best, score = "", 0.0
        for label, toks in held:
            if not toks or not want:
                continue
            ov = len(want & toks) / max(1, len(want))
            if ov > score:
                best, score = label, ov
        return best, round(score, 2)

    print("\n== download files vs library ==")
    results = []
    for path, size in cand:
        best, score = verdict(path)
        results.append({"file": path.rsplit("/", 1)[-1], "gb": round(size/1e9, 2),
                        "held": score >= 0.6, "score": score,
                        "match": best[:70]})
    for r in sorted(results, key=lambda r: (r["held"], -r["gb"])):
        flag = "HELD-DUPE" if r["held"] else "NEW?"
        print(f"  [{flag} {r['score']:.2f}] {r['gb']} GB {r['file'][:60]}")
        print(f"      closest: {r['match'][:70]}")

    print("\n== active torrents (progress>0 or complete, video-ish) ==")
    act = [t for t in qb.torrents() if (t.get("progress") or 0) > 0]
    print(f"torrents with any data: {len(act)}")
    for t in sorted(act, key=lambda t: -(t.get("progress") or 0))[:40]:
        nm = t.get("name") or ""
        if re.search(r"\.(mp3|flac|wav|ogg)$", nm, re.I) and len(nm) < 80:
            continue
        best, score = verdict(nm)
        flag = "HELD-DUPE" if score >= 0.6 else "NEW?"
        print(f"  [{flag} {score:.2f}] {round((t.get('progress') or 0)*100,1)}% "
              f"{t.get('state')} {round((t.get('size') or 0)/1e9,2)}GB :: {nm[:55]}")

    json.dump({"files": results},
              open("/root/.hermes/profiles/media-maintainer/media-ops/state/dupe-sweep.json", "w"),
              indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
