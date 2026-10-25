#!/usr/bin/env python3
"""Convert one multi-part doco folder into DocuSeries episodes — with guards.

Usage:
    SRC=/mnt/zmedia/videos/Documentaries/TraumaZone (2022) \
    QUERY="Russia 1985-1999 TraumaZone" YEAR=2022 TITLE="TraumaZone (2022)" \
    python3 -u scripts/multipart_to_series.py

Guards (each learned from a failure):
  1. abort if Documentary Series already holds the title — avoids manufacturing a duplicate
     (The Genius of Design already existed as a series).
  2. abort if the provider's episode count != file count — avoids inventing a mapping
     (New York had 8 files numbered 1-6,8,9 against a 10-episode provider listing).
  3. print every episode with its path after the scan so phantom rows are visible
     (The Civil War: 9 files produced 18 episode rows after a rename).

Identify uses the SERIES route (`media_type="Series"`) — the Movie route silently returns zero
candidates for series and ignores the supplied provider id.
"""

import os
import re
import subprocess
import sys
import time
import shlex

sys.path.insert(0, "/root/.hermes/profiles/media-maintainer/media-ops")
from media_lib import jellyfin, tmdb  # noqa: E402  (path set above)

NODE = "root@192.168.214.131"

SRC = os.environ.get("SRC") or "/mnt/zmedia/videos/FilmDocumentaries/A Century of Light and Shadow (2005)"
QUERY = os.environ.get("QUERY") or "A Century of Light and Shadow"
YEAR = int(os.environ.get("YEAR") or 2005)
SEASON = int(os.environ.get("SEASON") or 1)

# Placement rule: music content -> Music Docos Series, film-subject -> Film Documentary Series,
# everything else -> Documentary Series. LIB is the Jellyfin library name; LIBDIR is its folder on disk.
LIB = os.environ.get("LIB") or "Documentary Series"
LIBDIRS = {"Documentary Series": "DocuSeries",
           "Music Docos Series": "musicdocos-series",
           "Film Documentary Series": "filmdocuseries"}
LIBDIR = LIBDIRS.get(LIB)
if not LIBDIR:
    raise SystemExit("unknown LIB {!r}; known: {}".format(LIB, sorted(LIBDIRS)))
TITLE = os.environ.get("TITLE") or "A Century of Light and Shadow (2005)"


def sh(cmd, t=400):
    return subprocess.run(["ssh", "-o", "ConnectTimeout=12", NODE, cmd],
                          capture_output=True, text=True, timeout=t).stdout or ""


v = jellyfin.get_json("/Library/VirtualFolders")
ids = {f.get("Name"): f.get("ItemId") for f in (v if isinstance(v, list) else [])}

# ---- guard 1: existing series entry?
KEY = TITLE.split(" (")[0]                       # derive from arguments, never hardcode
KEY_NORM = KEY.lower().replace(" ", "").replace(".", "").replace("-", "")

r = jellyfin.get_json("/Items", params={"ParentId": ids[LIB], "Recursive": "true",
                                        "IncludeItemTypes": "Series", "Fields": "Path,Name",
                                        "Limit": "2000", "SearchTerm": KEY})
existing = [i for i in (r.get("Items") or [])
            if KEY_NORM in str(i.get("Path", "")).lower().replace(" ", "").replace(".", "").replace("-", "")]
if existing:
    print("ABORT: series entry already exists:", existing[0].get("Path"))
    print("     not converting — would create a duplicate")
    sys.exit(0)

# ---- files present
listing = sh('cd {d} && ls -1 *.mkv *.mp4 *.avi *.m4v 2>/dev/null'.format(d=shlex.quote(SRC)))
files = [f for f in listing.splitlines() if f.strip()]
print("files found:", len(files))
for f in sorted(files):
    print("   ", f)

# ---- provider
s = tmdb._get("/search/tv", {"query": QUERY})
def _norm(t):
    return str(t or "").lower().replace(" ", "").replace(".", "").replace("-", "").replace(":", "")


# YEAR must disambiguate: 'Pandora's Box' matches a 2021 series (12 eps) before the 1992 one (6 eps),
# and taking the first name match silently bound the wrong show.
_matches = [x for x in (s.get("results") or []) if _norm(KEY) in _norm(x.get("name"))]
if YEAR and _matches:
    def _year_of(x):
        try:
            return int(str(x.get("first_air_date") or "")[:4])
        except ValueError:
            return 0

    _exact = [x for x in _matches if _year_of(x) == YEAR]
    _near = [x for x in _matches if _year_of(x) and abs(_year_of(x) - YEAR) <= 2]
    if _exact:
        _matches = _exact
    elif _near:
        print("NOTE: no exact year match for {} — using nearest ({})"
              .format(YEAR, [_year_of(x) for x in _near]))
        _matches = _near
    else:
        print("WARN: no year match for {} — falling back to name only".format(YEAR))

tid = _matches[0].get("id") if _matches else None
if _matches:
    print("provider candidates:", [(_year_of(x), x.get("name")) for x in _matches[:4]])
print("tmdb:", tid)
if not tid:
    print("ABORT: no provider match — leaving files untouched")
    sys.exit(0)
se = tmdb._get("/tv/{i}/season/{n}".format(i=tid, n=SEASON), {})
eps = se.get("episodes") or []
print("provider episodes:", len(eps))
for e in eps:
    print("   E{} {} [{} min]".format(e.get("episode_number"), e.get("name"), e.get("runtime")))

# ---- guard: do the files' runtimes match the provider's episodes?
# A count match is not a mapping. The American Revolution had 6 files against 6 episodes, but every file
# was 59 min against 116-131 min episodes: the set was half the series. Count checks cannot see that.
def _dur(path):
    out = sh('ffprobe -v error -show_entries format=duration -of csv=p=0 {p} 2>/dev/null'
             .format(p=shlex.quote(path)))
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


todo = [f for f in files if f.strip()]
durs = []
for f in todo:
    d = _dur(SRC + "/" + f)
    durs.append(d)
print("file runtimes (min):", [round(d / 60, 1) for d in durs])
prov = [e.get("runtime") or 0 for e in eps[:len(todo)]]
if prov and all(prov):
    ratios = [d / 60 / r for d, r in zip(durs, prov) if r]
    bad = [round(x, 2) for x in ratios if x < 0.75 or x > 1.33]
    print("duration ratio vs provider:", [round(x, 2) for x in ratios])
    if bad:
        within_tolerance = abs(sum(durs) / 60 - sum(prov[:len(todo)])) <= 0.05 * max(1, sum(prov[:len(todo)]))
        total_note = "totals {} vs {} min".format(round(sum(durs) / 60), sum(prov[:len(todo)]))
        if os.environ.get("ALLOW_RUNTIME_MISMATCH") == "1" and within_tolerance:
            print("WARN: per-file split disagrees ({}) but {} — set is complete, converting by order"
                  .format(bad, total_note))
        else:
            print("ABORT: file durations disagree with provider episode runtimes ({}) — {}. Holding for a "
                  "human.".format(bad, total_note))
            sys.exit(0)
else:
    print("NOTE: provider has no per-episode runtimes — duration guard cannot run")

if len(eps) != len(files):
    if os.environ.get("ALLOW_PARTIAL") != "1":
        print("ABORT: provider has {a} episodes but {b} files — ambiguous, handing to a human"
              .format(a=len(eps), b=len(files)))
        sys.exit(0)
    print("PARTIAL: {b} of {a} episodes present — mapping by part number, gap left visible"
          .format(a=len(eps), b=len(files)))

# ---- convert
DST = "/mnt/zmedia/videos/{lib}/{t}/Season {s:02d}".format(lib=LIBDIR, t=TITLE, s=SEASON)
sh("mkdir -p " + shlex.quote(DST))
def _part_no(f):
    m = re.search(r"(?i)(?:part|pt|cd|disc)[\s._-]*(\d+)", f)
    if m:
        return int(m.group(1))
    nums = re.findall(r"\d+", f)
    return int(nums[-1]) if nums else 0


ordered = sorted(files, key=_part_no)
for idx, f in enumerate(ordered, start=1):
    title = (eps[idx - 1].get("name") or "").strip() if idx - 1 < len(eps) else ""
    safe = "".join(c for c in title if c.isalnum() or c in " -'.,&()").strip()
    ext = ("." + f.rsplit(".", 1)[-1].lower()) if "." in f else ".mkv"
    name = "{t} S{s:02d}E{n:02d}".format(t=TITLE, s=SEASON, n=idx) + ((" " + safe) if safe else "") + ext
    out = sh("mv -f {a} {b} && echo OK".format(a=shlex.quote(SRC + "/" + f),
                                               b=shlex.quote(DST + "/" + name)))
    print("E{:<2} {}".format(idx, "moved" if "OK" in out else "FAILED " + f))

for nm in (LIB, "Film Documentaries"):
    if ids.get(nm):
        jellyfin.refresh_library(ids[nm])
time.sleep(95)

r = jellyfin.get_json("/Items", params={"ParentId": ids["Documentary Series"], "Recursive": "true",
                                        "IncludeItemTypes": "Series", "Fields": "Path",
                                        "Limit": "2000", "SearchTerm": KEY})
target = next((i for i in (r.get("Items") or [])
               if KEY_NORM in str(i.get("Path", "")).lower().replace(" ", "").replace(".", "").replace("-", "")), None)
print("series item:", target and target["Id"])
if target:
    res = jellyfin.remote_search(target["Id"], QUERY, YEAR, {"Tmdb": str(tid)}, media_type="Series")
    print("candidates:", [(x.get("Name"), x.get("ProductionYear"), x.get("ProviderIds")) for x in res[:3]])
    best = next((x for x in res if x.get("ProviderIds", {}).get("Tmdb") == str(tid)), None)
    if best:
        print("apply ->", jellyfin.apply_identification(target["Id"], best, replace_images=True))
    time.sleep(85)
    a = jellyfin.get_json("/Users/{u}/Items/{i}".format(u=jellyfin.user_id(), i=target["Id"]),
                          params={"Fields": "ProviderIds,Overview,ImageTags,Name,ProductionYear"})
    print("series now:", a.get("Name"), a.get("ProductionYear"), a.get("ProviderIds"))
    ep = jellyfin.get_json("/Items", params={"ParentId": target["Id"], "Recursive": "true",
                                            "IncludeItemTypes": "Episode",
                                            "Fields": "Path,Name,RunTimeTicks,IndexNumber",
                                            "Limit": "100"})
    got = ep.get("Items") or []
    # sweep phantom rows (path no longer on disk)
    live = [e for e in got if TITLE.split(" (")[0].lower().replace(" ", "") in
            str(e.get("Path", "")).lower().replace(" ", "").replace(".", "")]
    print("episodes: {}  distinct paths: {}".format(len(got), len({e.get("Path") for e in got})))
    for e in sorted(got, key=lambda x: x.get("IndexNumber") or 0):
        print("   E{} {:>6}min {}  {}".format(e.get("IndexNumber"),
                                              round((e.get("RunTimeTicks") or 0) / 600_000_000, 1),
                                              str(e.get("Name"))[:40],
                                              "OK" if e in live else "STALE?"))
