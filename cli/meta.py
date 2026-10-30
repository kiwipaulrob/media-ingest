"""Stage — metadata enrichment (plan / apply).

plan   read-only: which credited people in a library lack a bio, and what can
       the provider chain fill?
apply  write bios for explicitly listed plan ids (or a bounded --limit),
       never-clobbering rows that gained a bio since the plan was built.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List

from media_lib import enrich, jellyfin, safety

MUSIC_DOCOS = "5f95760bae4c07e31d840d70c3a46111"


def _plan(args) -> int:
    out = enrich.build_plan(jellyfin.resolve_library(args.library), limit=args.limit)
    print(f"people with no bio: {out.get('people_gaps', 0)}")
    print(f"  ready (>=80 chars): {out['ready']}")
    print(f"  thin  (15-79):      {out['thin']}")
    print(f"  suspect (NOT safe): {out.get('suspect', 0)}")
    print(f"  review-birth:       {out.get('review_birth', 0)}")
    print(f"  gap   (nothing):    {out['gap']}")
    print(f"  no person row:      {out['no_row']}")
    top = [r for r in out["rows"] if r["status"] in ("ready", "thin")][:25]
    for r in top:
        print(
            f"   {r['status']:<6} {r.get('bio_len', 0):>4}c via {str(r.get('source')):<10} "
            f"{r['name'][:38]}"
        )
    # Suspects are shown, never hidden: they look like ready bios and that is the
    # point — a numbered sense list or a musician answering a crew credit. They
    # must be read by a human, not handed to a sweep.
    sus = [r for r in out["rows"] if r["status"] == "suspect"][:15]
    if sus:
        print("\n  SUSPECT — do not auto-apply:")
        for r in sus:
            print(
                f"   {r.get('bio_len', 0):>4}c via {str(r.get('source')):<10} "
                f"{r['name'][:30]:<32} {r.get('collision', '')}"
            )
    # Portraits are approval-gated (never auto-attached): list imageless rows
    # where the chain already found a portrait URL, so the human pick is one
    # `bio portrait --id ... --url ...` away. (Halstead gap: bios applied for
    # months while portraits sat in the plan rows unread.)
    po = [r for r in out["rows"]
          if not r.get("has_image") and r.get("portrait")][:20]
    if po:
        print("\n  PORTRAITS AVAILABLE — attach with `bio portrait --id <id> --url <url>`:")
        for r in po:
            print(f"   {r['name'][:34]:<36} via {str(r.get('portrait_source')):<10} "
                  f"{str(r.get('portrait'))[:80]}")
    return 0


def _identify(args) -> int:
    """Identify-first sweep (Moog gap): bare movies get credits before bios."""
    from media_lib import tmdb

    parent = jellyfin.resolve_library(args.library)
    bare = enrich.bare_movies(parent)
    print(f"bare movies (no credits, no ids): {len(bare)}")
    counts: Dict[str, int] = {}
    # The write lock is for writes: a dry-run holding it blocks every other
    # run (and the test suite) for the whole sweep. Lock only around --apply.
    from contextlib import nullcontext
    guard = safety.acquire() if args.apply else nullcontext()
    with guard:
        for i, b in enumerate(bare, 1):
            name = str(b.get("name") or "")
            g = tmdb.gate(f"{name} {b.get('year') or ''}".strip())
            cand = (g.get("candidate") or {})
            want = cand.get("id")
            if g.get("status") != "candidate" or not want:
                counts["SKIP-NO-CANDIDATE"] = counts.get("SKIP-NO-CANDIDATE", 0) + 1
                print(f"  [{i}/{len(bare)}] SKIP-NO-CANDIDATE {name[:44]}", flush=True)
                continue
            if not args.apply:
                counts["WOULD"] = counts.get("WOULD", 0) + 1
                print(f"  [{i}/{len(bare)}] WOULD tmdb={want} {name[:44]}", flush=True)
                continue
            try:
                yr = b.get("year")
                found = jellyfin.remote_search(
                    str(b.get("id")), name,
                    int(yr) if yr else None,
                    {"Tmdb": str(want)})
                pick = enrich.pick_identification(name, b.get("year"), found or [], want)
                if not pick:
                    # Providers hold nothing (6/6 on the first live run): pin
                    # the gated id + TMDB overview directly instead of failing.
                    # Credits cannot be fetched, but the row leaves the bare
                    # set and future refreshes have an id to work from.
                    from media_lib import tmdb as _tmdb
                    d = _tmdb._get(f"/movie/{want}", {}) or {}
                    if d.get("overview"):
                        cur = jellyfin.get_json(
                            f"/Users/{jellyfin.user_id()}/Items/{b.get('id')}") or {}
                        if not (cur.get("Overview") or "").strip():
                            jellyfin.set_overview(str(b.get("id")), d["overview"],
                                                  lock=True)
                    uid = jellyfin.user_id()
                    dto = jellyfin.get_json(f"/Users/{uid}/Items/{b.get('id')}") or {}
                    prov = dto.get("ProviderIds") or {}
                    prov["Tmdb"] = str(want)
                    dto["ProviderIds"] = prov
                    jellyfin.post_item(str(b.get("id")), dto)
                    counts["PINNED"] = counts.get("PINNED", 0) + 1
                    print(f"  [{i}/{len(bare)}] PINNED tmdb={want} {name[:44]}", flush=True)
                    continue
                jellyfin.apply_identification(str(b.get("id")), pick)
                counts["DONE"] = counts.get("DONE", 0) + 1
                print(f"  [{i}/{len(bare)}] DONE tmdb={want} {name[:44]}", flush=True)
            except Exception as exc:  # noqa: BLE001
                counts["ERR"] = counts.get("ERR", 0) + 1
                print(f"  [{i}/{len(bare)}] ERR {name[:40]} {str(exc)[:80]}", flush=True)
    print("totals:", counts)
    return 0


def _overviews(args) -> int:
    """Backfill item overviews from pinned TMDB ids (One-More-Time gap)."""
    from media_lib import tmdb
    from contextlib import nullcontext

    parent = jellyfin.resolve_library(args.library)
    gaps = enrich.thin_overviews(parent)
    print(f"thin overviews: {len(gaps)}")
    counts: Dict[str, int] = {}
    guard = safety.acquire() if args.apply else nullcontext()
    with guard:
        for i, g in enumerate(gaps, 1):
            name = str(g.get("name") or "")
            try:
                d = tmdb._get(f"/movie/{g.get('tmdb')}", {}) or {}
                ov = (d.get("overview") or "").strip()
                if not ov:
                    counts["SKIP-TMDB-BLANK"] = counts.get("SKIP-TMDB-BLANK", 0) + 1
                    print(f"  [{i}/{len(gaps)}] SKIP-TMDB-BLANK {name[:44]}", flush=True)
                    continue
                if not args.apply:
                    counts["WOULD"] = counts.get("WOULD", 0) + 1
                    print(f"  [{i}/{len(gaps)}] WOULD {len(ov)}ch {name[:44]}", flush=True)
                    continue
                jellyfin.set_overview(str(g.get("id")), ov, lock=True)
                counts["DONE"] = counts.get("DONE", 0) + 1
                print(f"  [{i}/{len(gaps)}] DONE {len(ov)}ch {name[:44]}", flush=True)
            except Exception as exc:  # noqa: BLE001
                counts["ERR"] = counts.get("ERR", 0) + 1
                print(f"  [{i}/{len(gaps)}] ERR {name[:40]} {str(exc)[:80]}", flush=True)
    print("totals:", counts)
    return 0


def _apply(args) -> int:
    plan = json.loads(enrich.PLAN.read_text())
    rows: List[Dict[str, Any]] = plan.get("rows") or []
    if args.ids:
        wanted = {int(x) for x in args.ids.split(",") if x.strip()}
        rows = [r for i, r in enumerate(rows, 1) if i in wanted]
    else:
        rows = [r for r in rows if r["status"] in ("ready", "thin")]
        if args.limit:
            rows = rows[: args.limit]
    if not rows:
        print("nothing to apply", file=sys.stderr)
        return 2

    print(f"applying {len(rows)} bio(s)", flush=True)
    counts: Dict[str, int] = {}
    with safety.acquire():
        for i, r in enumerate(rows, 1):
            if r["status"] not in ("ready", "thin") or not r.get("id"):
                counts["SKIP-NOT-READY"] = counts.get("SKIP-NOT-READY", 0) + 1
                continue
            try:
                res = enrich.apply_person(r["id"], r["bio"], r.get("source") or "chain")
            except Exception as exc:  # noqa: BLE001
                res = {"status": "ERR", "chars": 0, "err": str(exc)[:100]}
            counts[res["status"]] = counts.get(res["status"], 0) + 1
            print(
                f"  [{i}/{len(rows)}] {res['status']:<13} {res.get('chars', 0):>4}c "
                f"{r['name'][:40]}",
                flush=True,
            )
    print("totals:", counts)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Metadata enrichment")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="read-only gap + capability report")
    p.add_argument("--library", default=MUSIC_DOCOS)
    p.add_argument("--limit", type=int, default=None)
    a = sub.add_parser("apply", help="write bios for planned rows")
    a.add_argument("--ids", default="", help="comma-separated plan row ids")
    a.add_argument("--limit", type=int, default=None)
    i = sub.add_parser("identify", help="identify bare movies (dry-run unless --apply)")
    i.add_argument("--library", default=MUSIC_DOCOS)
    i.add_argument("--apply", action="store_true")
    o = sub.add_parser("overviews", help="backfill filename-as-overview rows (dry-run unless --apply)")
    o.add_argument("--library", default=MUSIC_DOCOS)
    o.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "plan":
        return _plan(args)
    if args.cmd == "identify":
        return _identify(args)
    if args.cmd == "overviews":
        return _overviews(args)
    return _apply(args)


if __name__ == "__main__":
    sys.exit(main())
