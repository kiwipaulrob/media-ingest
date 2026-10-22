"""Bio operations: one entry point instead of three drift-prone scripts.

Replaces the standalone sweep scripts that used to live in the skill directory
(`bio-worklist.py`, `bio-run.py`, `alias-run.py`). Those re-implemented provider
lookups and Jellyfin writes that `media_lib.providers.chain` and
`media_lib.enrich` already do — the same duplication that produced three separate
bugs when four hand-rolled hunt scripts were folded into `media_lib.source`.

Subcommands
-----------
  worklist  read-only: which credited people have no bio, and what the chain can fill
  fill      write planned bios (never clobbers, locks Name+Overview, journals)
  alias     write user-approved alias bios from a JSON file (prose only for bands)

Every write goes through `enrich.apply_person`, which is where the never-clobber
guard, the lock, the journal and the read-back already live and are tested.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from media_lib import enrich
from media_lib.providers import chain

MUSIC_DOCOS = "5f95760bae4c07e31d840d70c3a46111"
ALIAS_FILE = enrich.PLAN.parent / "alias-approved.json"
UA = {"User-Agent": "hermes-media-maintainer/1.0 (media library maintenance)"}


def _wiki_batch(titles: List[str]) -> Dict[str, str]:
    """Fetch intro extracts for up to 20 titles per request.

    Batched on purpose: one call per 20 names is what makes a 30-name queue finish
    in minutes, and a per-name loop is what used to trip the Wikimedia throttle
    into minutes-per-name backoffs.
    """
    out: Dict[str, str] = {}
    for i in range(0, len(titles), 20):
        chunk = titles[i:i + 20]
        q = urllib.parse.urlencode({
            "action": "query", "format": "json", "redirects": 1,
            "titles": "|".join(chunk), "prop": "extracts",
            "exintro": 1, "explaintext": 1,
        })
        try:
            with urllib.request.urlopen(
                urllib.request.Request("https://en.wikipedia.org/w/api.php?" + q, headers=UA),
                timeout=45,
            ) as fh:
                data = json.loads(fh.read())
        except Exception:  # noqa: BLE001 - a failed batch is a gap, not a crash
            continue
        qc = data.get("query", {}) or {}
        by_title = {p.get("title") or "": p for p in (qc.get("pages") or {}).values()}
        for title, page in by_title.items():
            text = re.sub(r"\s+", " ", (page.get("extract") or "")).strip()
            if title and text:
                out[title] = text

        # The API answers with the RESOLVED title, not the one asked for. An alias
        # target is very often a redirect ('The Dakotas' -> 'Dakotas'), so a bare
        # `out[requested]` lookup called an existing page a gap — a silent miss on
        # exactly the names this command exists to serve.
        norm = {n["from"]: n["to"] for n in (qc.get("normalized") or [])}
        redir = {r["from"]: r["to"] for r in (qc.get("redirects") or [])}

        def resolve(title: str, depth: int = 0) -> str:
            if depth > 5:
                return title
            for table in (norm, redir):
                if title in table:
                    return resolve(table[title], depth + 1)
            return title

        for requested in chunk:
            final = resolve(requested)
            if final in out and requested not in out:
                out[requested] = out[final]
    return out


def cmd_worklist(args) -> int:
    plan = enrich.build_plan(args.library, limit=args.limit, out_path=args.out)
    rows = plan.get("rows") or []
    planned = len(rows)
    print(f"people with no bio: {plan.get('people_gaps', 0)} (planned this run: {planned})")
    print(f"  ready {plan.get('ready', 0)} | thin {plan.get('thin', 0)} "
          f"| suspect {plan.get('suspect', 0)} | review-birth {plan.get('review_birth', 0)} "
          f"| gap {plan.get('gap', 0)} | no-row {plan.get('no_row', 0)}")
    for r in rows:
        if r["status"] == "no-row":
            continue
        print(f"  {r['status']:<12} {r.get('bio_len', 0):>4}c "
              f"{str(r.get('source') or '-'):<10} {r['name'][:44]}")
    return 0


def cmd_fill(args) -> int:
    plan = json.loads(Path(args.plan).read_text())
    rows: List[Dict[str, Any]] = plan.get("rows") or []
    if args.ids:
        wanted = {int(x) for x in args.ids.split(",") if x.strip()}
        rows = [r for i, r in enumerate(rows, 1) if i in wanted]
    else:
        # `suspect` is excluded by design: it looks ready and is not.
        rows = [r for r in rows if r.get("status") in ("ready", "thin")]
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("nothing to apply", file=sys.stderr)
        return 2
    counts: Dict[str, int] = {}
    for r in rows:
        if not r.get("id"):
            counts["NO-ROW"] = counts.get("NO-ROW", 0) + 1
            continue
        res = enrich.apply_person(r["id"], r.get("bio") or "", r.get("source") or "")
        counts[res["status"]] = counts.get(res["status"], 0) + 1
    print(json.dumps(counts, sort_keys=True))
    return 0


def cmd_alias(args) -> int:
    pairs = json.loads(Path(args.file).read_text())
    if not pairs:
        print("no approved pairs", file=sys.stderr)
        return 2
    fetched = _wiki_batch([p["wiki_title"] for p in pairs])
    print(f"approved pairs: {len(pairs)} | targets with prose: {len(fetched)}")

    index = enrich.person_rows()   # one scan, not one per name
    counts: Dict[str, int] = {}
    for p in pairs:
        name = p["name"]
        text = fetched.get(p["wiki_title"]) or ""
        if not text:
            counts["GAP-NO-PROSE"] = counts.get("GAP-NO-PROSE", 0) + 1
            print(f"  GAP-NO-PROSE {name}")
            continue
        # Screen before writing. The alias path used to hand the extract straight to
        # apply_person, so an approved target that happens to be a disambiguation page
        # or a pre-1900 life would be written as a biography — the exact class the
        # rest of the pipeline now rejects.
        bad = chain.bio_implausible(text)
        if bad:
            counts[f"REJECT-{bad}"] = counts.get(f"REJECT-{bad}", 0) + 1
            print(f"  REJECT-{bad} {name}")
            continue
        row = index.get(name)
        if not row:
            counts["NO-ROW"] = counts.get("NO-ROW", 0) + 1
            print(f"  NO-ROW {name}")
            continue
        # A band page carries group prose; it is fine as text but its image is a
        # group photo, so portraits are never taken from here (handled by callers).
        res = enrich.apply_person(row["Id"], text, "wikipedia")
        counts[res["status"]] = counts.get(res["status"], 0) + 1
        print(f"  {res['status']:<14} {name}"
              + ("  [band page: prose only]" if p.get("band_page") else ""))
    print(json.dumps(counts, sort_keys=True))
    return 0


def cmd_portrait(args) -> int:
    """Attach a portrait to an imageless person row.

    Guards that matter: a shared Last.fm placeholder and a group photo on a band
    page are both "an image" and both wrong. Callers pass the URL; this checks
    size and content type, and never overwrites an existing portrait.
    """
    from media_lib import jellyfin

    row = jellyfin.get_json(f"/Users/{jellyfin.user_id()}/Items/{args.id}",
                            params={"Fields": "ImageTags"})
    if (row.get("ImageTags") or {}).get("Primary") and not args.force:
        print("SKIP-HAS-IMAGE", file=sys.stderr)
        return 2
    try:
        with urllib.request.urlopen(urllib.request.Request(args.url, headers=UA), timeout=60) as fh:
            raw = fh.read()
            ctype = fh.headers.get("Content-Type", "")
    except Exception as exc:  # noqa: BLE001
        print(f"FETCH-FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if "svg" in ctype.lower() or raw[:5].lstrip().startswith(b"<svg"):
        print("REJECT svg", file=sys.stderr)
        return 1
    if len(raw) < 15_000:
        print(f"REJECT too small ({len(raw)} bytes) — likely a placeholder", file=sys.stderr)
        return 1
    try:
        status = jellyfin.set_primary_image(args.id, raw)
        served = jellyfin.get_bytes(f"/Items/{args.id}/Images/Primary/0")
    except Exception as exc:  # noqa: BLE001
        print(f"UPLOAD-FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"uploaded {len(raw)} bytes (http {status}); served {len(served)}")
    return 0 if len(served) == len(raw) else 1


def _name_matches(name: str, label: str) -> bool:
    """Is `label` the same name as `name`? Punctuation/order-insensitive, strict."""
    def norm(v: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (v or "").lower()).strip()

    a, b = norm(name), norm(label)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _wikidata_line(name: str) -> str:
    """One-line occupation description, or "" — cheapest possible fallback."""
    q = urllib.parse.urlencode({"action": "wbsearchentities", "format": "json",
                                "language": "en", "search": name, "limit": 1})
    try:
        with urllib.request.urlopen(
            urllib.request.Request("https://www.wikidata.org/w/api.php?" + q, headers=UA),
            timeout=30,
        ) as fh:
            hits = json.loads(fh.read()).get("search") or []
    except Exception:  # noqa: BLE001
        return ""
    if not hits:
        return ""
    desc = (hits[0].get("description") or "").strip()
    # The old gate compared only the SURNAME against the label, and fell back to the
    # requested name when the label was missing — so an unlabelled hit was accepted
    # with no check at all. Wikidata search is fuzzy; an unmatched entity means a
    # one-liner about the wrong person, which is the failure this whole pass exists
    # to prevent. No label => cannot verify => skip.
    if not desc or not _name_matches(name, hits[0].get("label") or ""):
        return ""
    return f"{name} is described by Wikidata as: {desc}."


def cmd_wikidata(args) -> int:
    """Fill honest one-liners from Wikidata where no prose exists.

    Deliberately short: an obviously-thin Overview is a gap a later pass can
    improve, whereas an invented biography is damage nobody can spot.
    """
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    index = enrich.person_rows()
    counts: Dict[str, int] = {}
    for name in names:
        row = index.get(name)
        if not row:
            counts["NO-ROW"] = counts.get("NO-ROW", 0) + 1
            continue
        line = _wikidata_line(name)
        if not line:
            counts["GAP"] = counts.get("GAP", 0) + 1
            continue
        res = enrich.apply_person(row["Id"], line, "wikidata")
        counts[res["status"]] = counts.get(res["status"], 0) + 1
        print(f"  {res['status']:<12} {name}: {line[:70]}")
    print(json.dumps(counts, sort_keys=True))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("worklist", help="read-only gap list")
    w.add_argument("--library", default=MUSIC_DOCOS)
    w.add_argument("--limit", type=int)
    w.add_argument("--out", type=Path, help="write the plan here instead of the shared artefact")
    w.set_defaults(func=cmd_worklist)

    f = sub.add_parser("fill", help="write planned bios")
    f.add_argument("--plan", default=str(enrich.PLAN))
    f.add_argument("--ids", help="comma-separated plan row numbers")
    f.add_argument("--limit", type=int)
    f.set_defaults(func=cmd_fill)

    a = sub.add_parser("alias", help="write approved alias bios")
    a.add_argument("--file", default=str(ALIAS_FILE))
    a.set_defaults(func=cmd_alias)

    po = sub.add_parser("portrait", help="attach a portrait to an imageless row")
    po.add_argument("--id", required=True)
    po.add_argument("--url", required=True)
    po.add_argument("--force", action="store_true", help="replace an existing image")
    po.set_defaults(func=cmd_portrait)

    wd = sub.add_parser("wikidata", help="fill one-liners where no prose exists")
    wd.add_argument("--names", required=True, help="comma-separated")
    wd.set_defaults(func=cmd_wikidata)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
