"""Hunt FEATURE FILMS through Prowlarr (torrent + usenet) — plan first, then queue.

Read-only by default: writes a numbered shortlist so a human picks by number,
exactly like `cli.source plan` / `cli.source queue` do for music docs.

Everything mechanical comes from the suite, not from here: `source.fetch_pool`
(the Prowlarr sweep), `source.candidates_by_words` (token matching),
`source.is_video_posting` (audio/book/trailer refusal), `source.hash_of` /
`source.queue_url` (magnet + infohash resolution), and `source.queue` (add,
force-start, tracker healing, verify-by-title).

What is film-specific and therefore lives here:

* **TMDB canonicalisation.** A film needs an authoritative title+year before it
  is matched against anything, so the hunt queries the canonical pair as well as
  the requested one.
* **Identity gates**, in the order that matters: no episodes, no featurettes or
  trailers, no packs, year within one, and an ambiguous title must carry its
  year (otherwise every same-named TV episode and remake passes).
* **1080p preferred over 2160p.** `source.rank` promotes 4K, which for a
  catalogue this old is mostly upscales.

  python3 scripts/hunt_films.py plan  --targets state/film-targets.json
  python3 scripts/hunt_films.py queue --ids 3,7 --plan state/film-plan.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media_lib import source, tmdb  # noqa: E402

STOP = {"the", "a", "an", "of", "and", "in", "on", "by", "to", "for", "from", "at"}
JUNK = re.compile(
    r"making[- ._]?of|featurette|behind[- ._]?the[- ._]?scenes|interview|"
    r"trailer|teaser|commentary|soundtrack|\bost\b|\bscore\b|\bmp3\b|\bflac\b|"
    r"\baudio\b|\bbook\b|audiobook|\bsubs?\b|\bsubtitles?\b|\bsample\b|"
    r"review|explained|documentary about|\bhd ?cam\b|\bcam ?rip\b|\btelesync\b|"
    r"\bts rip\b|\bpdvd\b|\b\d{2,3} ?mb\b",
    re.I,
)
EPISODIC = re.compile(r"\bS\d{1,2}\s?E\d{1,2}\b|\bS\d{1,2}\b.*\bE\d{1,2}\b", re.I)
#: Collections are the film shape of a "pack": `source.is_pack` cannot help here
#: because it means "not an episode", which is true of every film, and its part
#: detector reads the leading number of `711 Ocean Drive` as a part marker.
COLLECTION = re.compile(
    r"\b(collection|collector'?s set|box ?set|bundle|trilogy|quadrilogy|"
    r"anthology|\d+\s*(?:movie|film)s?)\b|\b(?:cd|disc|disk)\s?\d\b",
    re.I,
)
MIN_GB, MAX_GB = 0.30, 25.0
#: Lower is better. 4K last: for these catalogues it is nearly always an upscale.
RES_PREF = {"1080p": 0, "720p": 2, "2160p": 3, "480p": 4}


def tokens(text: str) -> list:
    return [t for t in source.norm(text).split() if t and t not in STOP]


def years_in(title: str) -> list:
    return [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", title or "")]


def resolution(title: str) -> str:
    t = (title or "").lower()
    for r in RES_PREF:
        if r in t:
            return r
    return ""


def tmdb_canon(title: str, year):
    """Pin proper title/year through TMDB so the hunt uses the real identity."""
    try:
        hit = tmdb.search(title, str(year) if year else None, "movie")
        if not hit:
            return None
        rel = (hit.get("release_date") or "")[:4]
        return {
            "title": hit.get("title"),
            "year": int(rel) if rel.isdigit() else year,
            "id": hit.get("id"),
        }
    except Exception:  # noqa: BLE001 - a TMDB outage must not abort a hunt
        return None


def film_picks(pool, want_title: str, want_year, out: dict) -> None:
    """Keep the postings that really are this film; fill `out` keyed by release."""
    for c in source.candidates_by_words(pool, [want_title], min_seeds=0, require_all=True):
        title = c.get("title") or ""
        if JUNK.search(title) or EPISODIC.search(title) or COLLECTION.search(title):
            continue
        ys = years_in(title)
        if ys and want_year and not any(abs(y - want_year) <= 1 for y in ys):
            continue
        # An ambiguous title must prove itself with a year: 'M' alone matches
        # a hundred unrelated postings, 'M 1951' matches the film.
        if not ys and want_year and len(tokens(want_title)) <= 2:
            continue
        gb = c.get("gb") or 0
        if not (MIN_GB <= gb <= MAX_GB):
            continue
        if (c.get("protocol") or "") == "torrent" and not (c.get("seeds") or 0):
            continue
        c["res"] = resolution(title)
        c["has_year"] = bool(ys)
        out.setdefault(source.norm(title) + str(gb), c)


def plan(targets, out_path) -> int:
    rows = []
    for t in targets:
        canon = tmdb_canon(t["title"], t.get("year"))
        want_title = (canon or {}).get("title") or t["title"]
        want_year = (canon or {}).get("year") or t.get("year")
        got: dict = {}
        queries = [f"{want_title} {want_year}" if want_year else want_title]
        if source.norm(t["title"]) != source.norm(want_title):
            queries.append(t["title"])
        for q in queries:
            pool = source.fetch_pool([q])
            film_picks(pool, want_title, want_year, got)
            if source.norm(t["title"]) != source.norm(want_title):
                film_picks(pool, t["title"], want_year, got)
        picks = sorted(
            got.values(),
            key=lambda c: (
                RES_PREF.get(c["res"], 9),
                -int(c["has_year"]),
                -(c.get("seeds") or 0),
                -(c.get("gb") or 0),
            ),
        )
        rows.append(
            {
                "title": t["title"],
                "year": t.get("year"),
                "tmdb": canon,
                "picks": picks[:6],
                "n": len(picks),
            }
        )
        best = picks[0]["title"][:64] if picks else "MISS"
        print(f'{t["title"]} ({t.get("year")}) [{len(picks)}] {best}', flush=True)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({"at": int(time.time()), "rows": rows}, indent=1))
    hits = sum(1 for r in rows if r["picks"])
    print(f"\nfound {hits}/{len(rows)} -> {out_path}")
    return 0 if hits else 1


def queue(plan_path, ids, picks=None) -> int:
    """Queue row `ids` (1-based) using the pick index in `picks` (default best)."""
    rows = json.loads(Path(plan_path).read_text())["rows"]
    picks = picks or {}
    done = []
    for i in ids:
        row = rows[i - 1]
        if not row["picks"]:
            print(f"[{i}] no picks — skip")
            continue
        k = picks.get(i, 0)
        if k >= len(row["picks"]):
            print(f"[{i}] pick {k} out of range — skip")
            continue
        pick = row["picks"][k]
        res = source.queue(pick, category="movies", force=True)
        res.update({"id": i, "wanted": row["title"], "picked": pick["title"]})
        done.append(res)
        print(
            f'[{i}] {row["title"]}: ok={res.get("ok")} verified={res.get("verified")} '
            f'{res.get("state", "")} trackers={res.get("trackers")} {res.get("reason", "")}'
        )
    Path("state/film-queue.jsonl").parent.mkdir(parents=True, exist_ok=True)
    with open("state/film-queue.jsonl", "a") as fh:
        for d in done:
            fh.write(json.dumps(d) + "\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "queue"])
    ap.add_argument("--targets", default="state/film-targets.json")
    ap.add_argument("--plan", default="state/film-plan.json")
    ap.add_argument("--ids", default="")
    ap.add_argument(
        "--picks",
        default="",
        help="row:index overrides, e.g. 2:2,13:2 (1-based row:0-based pick)",
    )
    a = ap.parse_args()
    if a.cmd == "plan":
        return plan(json.loads(Path(a.targets).read_text()), a.plan)
    ids = [int(x) for x in a.ids.split(",") if x.strip()]
    if not ids:
        raise SystemExit("--ids required")
    picks = {}
    for pair in a.picks.split(","):
        if ":" in pair:
            r, k = pair.split(":", 1)
            picks[int(r)] = int(k)
    return queue(a.plan, ids, picks)


if __name__ == "__main__":
    raise SystemExit(main())
