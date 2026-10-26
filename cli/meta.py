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
    args = ap.parse_args(argv)
    return _plan(args) if args.cmd == "plan" else _apply(args)


if __name__ == "__main__":
    sys.exit(main())
