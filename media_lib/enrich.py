"""Person enrichment: plan, then apply bios surgically.

Rules enforced here, each from a real incident:

* **Never clobber** — a row that gained a bio since the plan was built is
  skipped, not overwritten.
* **Surgical write** — only `Overview` and `LockedFields` change. `Genres` is
  omitted (its shape 400s the whole update) and `People` is never touched.
* **Journal per item** — a killed run must not forget what it wrote.
* **Id-gated** — `apply` takes explicit person ids from a plan; no "all".
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config, jellyfin
from .providers import chain

JOURNAL = config.STATE_DIR / "meta-apply.jsonl"
PLAN = config.STATE_DIR / "meta-plan.json"
MIN_BIO = 80  # shorter than this is reported, not written


def _credited_people(
    uid: str, parent_id: str, with_types: bool = False
):
    """Credit counts per person, and (optionally) the credit types seen.

    The type matters because a musician-only provider answering a crew credit has
    returned a different human — see `chain.collision_reason`. Counts alone cannot
    tell a director from a session player.
    """
    counts: Dict[str, int] = {}
    types: Dict[str, Dict[str, int]] = {}
    for item in jellyfin.list_items(
        uid,
        recursive=True,
        include_types="Movie,Video",
        parent_id=parent_id,
        fields="People",
    ):
        for p in item.get("People") or []:
            n = p.get("Name")
            if n:
                counts[n] = counts.get(n, 0) + 1
                t = (p.get("Type") or "").strip()
                if t:
                    seen = types.setdefault(n, {})
                    seen[t] = seen.get(t, 0) + 1
    if with_types:
        return counts, types
    return counts


def person_rows(uid: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Public person-row index (name -> row). Load once, look up many.

    Exposed so callers do not re-scan twelve thousand rows per name — a per-name
    scan inside a loop is the difference between seconds and minutes.
    """
    return _person_rows(uid or jellyfin.user_id())


def _person_rows(uid: str) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for r in jellyfin.list_items(
        uid,
        recursive=True,
        include_types="Person",
        fields="Overview,ImageTags,ProviderIds",
    ):
        n = r.get("Name")
        if n and n not in rows:
            rows[n] = r
    return rows


def build_plan(
    parent_id: str,
    limit: Optional[int] = None,
    min_bio: int = MIN_BIO,
    out_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Read-only. Which credited people have no bio, and what can the chain fill?

    `out_path` exists because tests (and concurrent runs) must never be able to
    overwrite the real plan artefact — a unit test with fake rows once clobbered
    a completed 425-row plan.
    """
    plan_path = Path(out_path) if out_path else PLAN
    uid = jellyfin.user_id()
    counts, credit_types = _credited_people(uid, parent_id, with_types=True)
    rows = _person_rows(uid)

    # Parenthesised deliberately: `(x.get('Overview') or '') or '' == ''` parses
    # as `x or ('' == '')` and is True for EVERY row, which silently turns a
    # ~420-name gap list into all 2,720 credited names.
    def _no_bio(row: Optional[Dict[str, Any]]) -> bool:
        return not str((row or {}).get("Overview") or "").strip()

    gaps = [(n, c) for n, c in counts.items() if _no_bio(rows.get(n))]
    gaps.sort(key=lambda x: -x[1])
    if limit:
        gaps = gaps[:limit]

    plan: List[Dict[str, Any]] = []
    for i, (name, films) in enumerate(gaps, 1):
        row = rows.get(name) or {}
        entry: Dict[str, Any] = {
            "name": name,
            "films": films,
            "id": row.get("Id"),
            "has_image": bool((row.get("ImageTags") or {}).get("Primary")),
        }
        if not row.get("Id"):
            entry.update(status="no-row")
            plan.append(entry)
            continue
        try:
            res = chain.resolve(name, "artist")
            if res["bio_len"] == 0:
                res = chain.resolve(name, "person")
        except Exception as exc:  # noqa: BLE001
            entry.update(status="error", error=str(exc)[:120])
            plan.append(entry)
            continue
        status = (
            "ready"
            if res["bio_len"] >= min_bio
            else ("thin" if res["bio_len"] else "gap")
        )
        if res.get("birth_suspect") and status in ("ready", "thin"):
            # Only a pre-1900 subject was found: almost certainly a different
            # person with the same name. Never auto-applied.
            status = "review-birth"
        # A candidate can pass the length bar and still not be a biography of this
        # person: Last.fm answers a shared name with a numbered sense list, and a
        # musician-only provider answering a crew credit returns a different human.
        # Both were being scored `ready` and handed to a future sweep.
        seen_types = credit_types.get(name) or {}
        primary_type = max(seen_types, key=seen_types.get) if seen_types else ""
        suspect = chain.collision_reason(
            str(res["fields"].get("biography") or ""),
            credit_type=primary_type,
            source=res["provenance"].get("biography"),
        )
        if suspect and status in ("ready", "thin"):
            status = "suspect"
        entry.update(
            status=status,
            collision=suspect or "",
            credit_type=primary_type,
            birth_year=res.get("birth_year"),
            birth_suspect=bool(res.get("birth_suspect")),
            bio_len=res["bio_len"],
            source=res["provenance"].get("biography"),
            portrait=res["fields"].get("portrait"),
            portrait_source=res["provenance"].get("portrait"),
            mbid=res["identity"].get("mbid"),
            conflicts=list(res["conflicts"]),
            rejected=[r.get("name") for r in res["rejected"]],
            bio=str(res["fields"].get("biography") or ""),
        )
        plan.append(entry)
        if i % 25 == 0:
            print(f"  planned {i}/{len(gaps)}", flush=True)
            # checkpoint: a kill must not discard completed work
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(
                json.dumps(
                    {
                        "partial": True,
                        "library": parent_id,
                        "planned": i,
                        "people_gaps": len(gaps),
                        "rows": plan,
                    },
                    indent=1,
                )
            )
        time.sleep(0.1)

    out = {
        "library": parent_id,
        "people_gaps": len(gaps),
        "rows": plan,
        "ready": sum(1 for r in plan if r["status"] == "ready"),
        "thin": sum(1 for r in plan if r["status"] == "thin"),
        "suspect": sum(1 for r in plan if r["status"] == "suspect"),
        "review_birth": sum(1 for r in plan if r["status"] == "review-birth"),
        "gap": sum(1 for r in plan if r["status"] == "gap"),
        "no_row": sum(1 for r in plan if r["status"] == "no-row"),
    }
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(out, indent=1))
    return out


def _journal(rec: Dict[str, Any]) -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


def apply_person(
    person_id: str, bio: str, source: str, lock: bool = True
) -> Dict[str, Any]:
    """Write one bio. Returns {status, chars} — never raises for a skip."""
    uid = jellyfin.user_id()
    row = jellyfin.item(uid, person_id, fields="Overview,LockedFields")
    if (row.get("Overview") or "").strip():
        return {"status": "SKIP-ARRIVED", "chars": len(row["Overview"])}
    before = (row.get("Name"), dict(row.get("ProviderIds") or {}))
    row["Overview"] = bio
    if lock:
        row["LockedFields"] = sorted(
            set((row.get("LockedFields") or []) + ["Name", "Overview"])
        )
    row.pop("Genres", None)
    try:
        jellyfin.post_item(person_id, row)
    except Exception:
        jellyfin.post_item(
            person_id,
            {
                "Name": row.get("Name"),
                "Overview": bio,
                "LockedFields": row.get("LockedFields", []),
            },
        )
    time.sleep(0.6)
    back = jellyfin.item(uid, person_id, fields="Overview,LockedFields")
    got = len(back.get("Overview") or "")
    clean = (
        back.get("Name") == before[0]
        and dict(back.get("ProviderIds") or {}) == before[1]
    )
    status = "DONE" if got >= MIN_BIO else ("PARTIAL" if got >= 15 else "WRITE-FAIL")
    rec = {
        "id": person_id,
        "name": back.get("Name"),
        "status": status,
        "chars": got,
        "source": source,
        "clean": clean,
    }
    _journal(rec)
    return rec
