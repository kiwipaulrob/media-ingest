#!/usr/bin/env python3
"""Move genuine TV series out of Film Documentaries (a movies library) into Film Docos Series.

Film Documentaries holds a mix: real multi-episode series, and single films split into parts, and films
with extras. Guessing from folder names produced errors earlier, so each candidate folder is checked
against TMDB: it moves only when TMDB has an exact-name TV series AND the episode count is plausible.

Everything skipped is reported with the reason, so a wrong call is visible rather than silent.
"""
import os
import re
import shlex
import subprocess
import sys
import time

sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import jellyfin, tmdb  # noqa: E402

NODE = "root@192.168.214.132"
ROOT = "/mnt/zmedia/videos/FilmDocumentaries"
DESTROOT = "/mnt/zmedia/videos/filmdocuseries"
VID = ("mkv", "mp4", "avi", "m4v")


def sh(cmd, t=500):
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=12", NODE, cmd],
                       capture_output=True, text=True, timeout=t)
    return r.stdout or ""


def flat(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def clean_title(name):
    t = re.sub(r"(?i)\[[^\]]*\]|\((?:\d{4}|tmdbid|imdbid|tvdbid)[^)]*\)", " ", name)
    t = re.sub(r"(?i)\b(pdtv|xvid|divx|dvdrip|1080p|720p|x264|x265|h264|ws|mvgroup)\b", " ", t)
    t = t.replace(".", " ").replace("_", " ")
    return re.sub(r"\s{2,}", " ", t).strip(" -")


def safe(s):
    return "".join(c for c in s if c.isalnum() or c in " -'.,&()").strip()   # no colon (breaks SMB)


# candidate folders: >=2 media files at depth 1
out = sh('find {r} -mindepth 1 -maxdepth 2 -type d 2>/dev/null'.format(r=shlex.quote(ROOT)))
folders = [d for d in out.splitlines() if d.strip()]
print("candidate folders:", len(folders))

libs = {x.get("Name"): x.get("ItemId") for x in (jellyfin.get_json("/Library/VirtualFolders") or [])}
moved, skipped = [], []

for d in folders:
    files = sh('find {d} -maxdepth 1 -type f \\( {p} \\) -printf "%p\\n" 2>/dev/null'
               .format(d=shlex.quote(d), p=" -o ".join('-iname "*.{}"'.format(e) for e in VID)))
    files = [f for f in files.splitlines() if f.strip()]
    if len(files) < 2:
        continue
    if not files:
        continue
    title = clean_title(os.path.basename(d))
    if len(title) < 6:
        continue
    s = tmdb._get("/search/tv", {"query": title}) or {}
    hit = next((r for r in (s.get("results") or [])
                if flat(r.get("name")) == flat(title)), None)
    if not hit:
        skipped.append((os.path.basename(d), f"{len(files)} files — no exact TMDB series"))
        continue
    det = tmdb._get("/tv/{i}".format(i=hit["id"]), {}) or {}
    eps = det.get("number_of_episodes") or 0
    if eps == 0:
        skipped.append((os.path.basename(d), "TMDB series has 0 episodes"))
        continue
    if len(files) > eps + 1:
        skipped.append((os.path.basename(d), f"{len(files)} files vs {eps} eps — too many, may be extras"))
        continue
    # move
    year = str(hit.get("first_air_date") or "")[:4]
    t_title = "{n} ({y})".format(n=safe(str(hit.get("name"))), y=year) if year else safe(str(hit.get("name")))
    dst = "{r}/{t}/Season 01".format(r=DESTROOT, t=t_title)
    sh("mkdir -p " + shlex.quote(dst))
    se = tmdb._get("/tv/{i}/season/1".format(i=hit["id"]), {}) or {}
    titles = {e.get("episode_number"): (e.get("name") or "") for e in (se.get("episodes") or [])}
    def _num(f):
        m = re.search(r"(\d+)", os.path.basename(f))
        return int(m.group(1)) if m else 0

    ordered = sorted(files, key=_num)
    for i, f in enumerate(ordered, start=1):
        ext = "." + f.rsplit(".", 1)[-1].lower()
        sub = safe(titles.get(i) or "")
        name = "{t} S01E{n:02d}".format(t=t_title, n=i) + ((" " + sub) if sub else "") + ext
        sh("mv -f {a} {b}".format(a=shlex.quote(f), b=shlex.quote(dst + "/" + name)))
    sh("rmdir {d} 2>/dev/null; true".format(d=shlex.quote(d)))
    moved.append((t_title, len(files), eps, hit["id"]))
    print(f"  MOVED {t_title}  {len(files)} files -> {eps}-episode series (tmdb {hit['id']})")

print(f"\nmoved: {len(moved)}   skipped: {len(skipped)}")
for t, why in skipped:
    print(f"  skip {t[:56]:<56} {why}")

# identify each moved series
if libs.get("Film Docos Series"):
    jellyfin.refresh_library(libs["Film Docos Series"])
    if libs.get("Film Docos"):
        jellyfin.refresh_library(libs["Film Docos"])
    time.sleep(95)
    for t_title, n, eps, tid in moved:
        r = jellyfin.get_json("/Items", params={"ParentId": libs["Film Docos Series"], "Recursive": "true",
                                                "IncludeItemTypes": "Series", "Fields": "Path",
                                                "Limit": "2000", "SearchTerm": t_title.split(" (")[0]})
        t = next((i for i in (r.get("Items") or []) if flat(t_title.split(" (")[0]) in flat(i.get("Path"))), None)
        if not t:
            print(f"  {t_title}: series item not found after scan")
            continue
        res = jellyfin.remote_search(t["Id"], t_title.split(" (")[0], None, {"Tmdb": str(tid)},
                                     media_type="Series")
        best = next((x for x in res if x.get("ProviderIds", {}).get("Tmdb") == str(tid)), None)
        if best:
            jellyfin.apply_identification(t["Id"], best, replace_images=True)
        jellyfin.refresh_item(t["Id"])
        time.sleep(45)
        a = jellyfin.get_json("/Users/{u}/Items/{i}".format(u=jellyfin.user_id(), i=t["Id"]),
                              params={"Fields": "ProviderIds,Overview,ImageTags,Name"})
        print(f"  {a.get('Name')}: prov={list((a.get('ProviderIds') or {}).keys())} "
              f"overview={len(a.get('Overview') or '')}ch images={list((a.get('ImageTags') or {}).keys())}")
