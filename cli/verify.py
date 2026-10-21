"""Stage 4 — verify.

The 4-field exit gate: Tmdb, Imdb, a primary image, and an overview longer than
50 chars. Reported field-by-field — a 3/4 is never presented as complete.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_lib import config, jellyfin


def gate(uid, item_id) -> dict:
    it = jellyfin.item(uid, item_id, fields="ProviderIds,ImageTags,Overview,Path,Name")
    prov = it.get("ProviderIds") or {}
    ov = (it.get("Overview") or "").strip()
    img = bool((it.get("ImageTags") or {}).get("Primary"))
    score = sum([bool(prov.get("Tmdb")), bool(prov.get("Imdb")), img, len(ov) > 50])
    return {
        "id": item_id,
        "name": it.get("Name"),
        "score": f"{score}/4",
        "tmdb": prov.get("Tmdb"),
        "imdb": prov.get("Imdb"),
        "image": img,
        "overview_len": len(ov),
        "path": it.get("Path"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check the 4-field exit gate")
    ap.add_argument("--ids", default="", help="comma-separated item ids")
    ap.add_argument(
        "--plan",
        default=str(config.STATE_DIR / "plan.json"),
        help="fall back to the plan's old_id values",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    if not ids:
        try:
            plan = json.loads(Path(args.plan).read_text())
            ids = [str(r["old_id"]) for r in plan if r.get("old_id")]
        except (OSError, json.JSONDecodeError):
            ids = []
    if not ids:
        print("no item ids to check", file=sys.stderr)
        return 2

    uid = jellyfin.user_id()
    rows = []
    for i in ids:
        try:
            rows.append(gate(uid, i))
        except Exception as exc:  # noqa: BLE001
            rows.append({"id": i, "score": "ERR", "error": str(exc)[:100]})

    if args.json:
        print(json.dumps(rows, indent=1))
        return 0
    for r in rows:
        if r.get("score") == "ERR":
            print(f"  {r['id']}  ERROR {r.get('error')}")
            continue
        print(
            f"  {r['score']}  tmdb={r['tmdb'] or '-':<8} imdb={r['imdb'] or '-':<11} "
            f"img={'Y' if r['image'] else '-'} ov={r['overview_len']:<4} {str(r['name'])[:44]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
