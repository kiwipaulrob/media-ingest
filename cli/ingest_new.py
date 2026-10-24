"""Stage 5 CLI — place completed downloads as NEW library items.

  python3 -m cli.ingest_new --spec state/new-placement.json [--dry-run]

The spec is a list of placements::

    {"src": "/downloads-zmedia/Some.Release.mkv",
     "dest": "/mnt/zmedia/videos/.../Season 02/Show S02E03 Name.mkv",
     "min_height": 700}

Every placement passes the same gates as a swap (copy, probe, duration, decode)
and a failure removes the staged copy. Journal entries land in
`state/ingest-new.jsonl` so a re-run is idempotent.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from media_lib import (awaiting, avsync, config, jellyfin, place, qb, quality, rowcheck,
                       safety, tmdb)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Place completed downloads as new items")
    ap.add_argument("--spec", required=False,
                    help="placement spec (required unless --backfill is used)")
    ap.add_argument("--fix-names", action="store_true",
                    help="repair a mis-resolved row name from TMDB (matched BY NAME) "
                         "and lock it; requires --tmdb-series")
    ap.add_argument("--tmdb-series", type=int, default=None,
                    help="TMDB series id used by --fix-names (e.g. 9048 Classic Albums)")
    ap.add_argument("--backfill", action="store_true",
                    help="record past placements from the journal into the ledger, then exit")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--refresh-lib", default=None, help="library id to refresh after placing"
    )
    ap.add_argument(
        "--wait-rows",
        type=int,
        default=0,
        metavar="SECS",
        help="TOTAL seconds to wait for rows (one deadline shared by all items)",
    )
    args = ap.parse_args(argv)

    config.ensure_state_dir()
    journal = config.STATE_DIR / "ingest-new.jsonl"
    if args.backfill:
        backfill_ledger(journal)
        return 0
    if not args.spec:
        ap.error("--spec is required unless --backfill is used")
    placements = json.loads(Path(args.spec).read_text())
    done = set()
    if journal.exists():
        for line in journal.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ok"):
                done.add(rec.get("dest"))

    results = []
    if args.dry_run:
        for p in placements:
            dest = p["dest"]
            if dest in done:
                print(f"SKIP   (already placed) {Path(dest).name}"[:110], flush=True)
                continue
            print(f'WOULD  {p["src"][-58:]}  ->  {Path(dest).name}', flush=True)
        return 0

    # Serialise with cli/apply.py and cli/meta.py: a second run acting on a plan built
    # before the first changed the library is how files get placed twice or lost.
    with safety.acquire(config.STATE_DIR / "intake.lock"):
        for p in placements:
            dest = p["dest"]
            if dest in done:
                print(f"SKIP   (already placed) {Path(dest).name}"[:110], flush=True)
                continue
            src = p["src"]
            torrent = None
            if p.get("hash"):
                src = qb.resolve_media_path(src, p["hash"]) or src
            else:
                # Look the torrent up by path: without a hash the ledger entry below
                # has no key, and `media_scan` re-offers an already-ingested release.
                try:
                    torrent = qb.torrent_for_path(src)
                except Exception:  # noqa: BLE001
                    torrent = None
                if torrent:
                    src = qb.resolve_media_path(src, torrent["hash"]) or src
            # IS THE DOWNLOAD EVEN BETTER? A title already on the shelf must not gain a
            # second copy just because a torrent finished. Place only when the library
            # holds nothing comparable, or when this copy measurably beats it; a
            # replacement is cli.apply's job, and an unmeasurable held copy is a review,
            # never a silent duplicate (uncertainty must not resolve to "ingest").
            label = Path(dest).parent.name
            try:
                verdict = quality.decide(Path(src).name, label)
                v = str(verdict.get("verdict"))
            except Exception as exc:  # noqa: BLE001
                v = f"check-failed:{type(exc).__name__}"
            if v in ("same-or-worse", "marginal", "different-title"):
                print(f"HELD-BETTER {Path(dest).name[:52]:<53} {v}", flush=True)
                continue
            if v.startswith("check-failed") or v in ("unknown", "held-unknown"):
                print(f"REVIEW       {Path(dest).name[:52]:<53} {v} (not ingested)", flush=True)
                continue

            res = place.place_new(
                src,
                dest,
                min_height=int(p.get("min_height") or 0),
                expect_duration=p.get("expect_duration"),
            )
            # place_new() sets dest, but this loop KNOWS the intended destination — carry
            # it explicitly so every result row is self-describing. Its absence was a
            # KeyError hidden by the broad excepts in the sync and notify blocks.
            res.setdefault("dest", dest)
            res["src"] = src
            res["at"] = int(time.time())
            with journal.open("a") as fh:
                fh.write(json.dumps(res) + "\n")
            results.append(res)
            if res["ok"]:
                # Journal AND ledger immediately: a killed run must not forget a
                # placement, and `scan` keys its exclusions off this ledger.
                rec_hash = p.get("hash") or (torrent or {}).get("hash")
                if rec_hash:
                    try:
                        safety.record(hash=rec_hash, action="place", dest=dest, src=src)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  LEDGER-WARN {type(exc).__name__}", flush=True)
            flag = "PLACED" if res["ok"] else "FAILED"
            extra = res.get("res") or res.get("detail") or res.get("stage")
            print(f"{flag:<7}{Path(dest).name[:58]:<59} {str(extra)[:40]}", flush=True)

    placed = [r for r in results if r.get("ok")]
    unverified = 0
    if placed:
        # A/V sync is checked on EVERY placement, not on request. A desynced file
        # still plays, so no later gate catches it — the viewer does, and by then
        # it is in the library and already half-watched. What is provable here is
        # the container-level fault set (offset, drift, missing track, disagreeing
        # tracks); subjective lip-sync is not testable from the file alone, so a
        # pass must not be read as "the dialogue looks right" (see media_lib.avsync).
        for r in placed:
            try:
                verdict = avsync.check(place.to_disk(r["dest"]))
            except Exception as exc:  # noqa: BLE001 - the file is already placed
                # The copy is on disk, so refusing is no longer possible — but recording
                # "unverified" in the journal and returning non-zero is. Swallowing this
                # into a printed line is how a skipped sync check looks identical to a pass.
                r["sync"] = "unverified"
                r["sync_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                unverified += 1
                print(f"  SYNC-UNVERIFIED {type(exc).__name__}: {str(exc)[:80]}", flush=True)
                continue
            r["sync"] = verdict.get("verdict")
            if verdict.get("verdict") != "ok":
                print(f"  {avsync.describe(verdict)} — {Path(r['dest']).name[:46]}", flush=True)
        # A library Refresh re-resolves KNOWN rows; it does NOT discover new
        # files. Without this call a placement stays invisible until the next
        # full discovery — proven the hard way on two episodes.
        try:
            jellyfin.notify_media([place.to_jf(r["dest"]) for r in placed])
            print(f"notified scanner of {len(placed)} new file(s)", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"NOTIFY-FAIL {type(exc).__name__}: {exc}", flush=True)
        if args.wait_rows:
            # ONE global deadline shared by every item. The previous version
            # polled per item (and, after a bad patch, twice over), which held
            # the run open past the caller's timeout and reported healthy
            # placements as failures. Row discovery has taken ~9 minutes under
            # a drained backlog, so a miss is "not yet", never "failed".
            found = awaiting.await_rows(placed, budget=args.wait_rows)
            with journal.open("a") as fh:
                for r in placed:
                    info = found.get(r["dest"], {})
                    r["item_id"] = info.get("item_id", "")
                    print(
                        f"  row {'found' if r['item_id'] else 'NOT YET'} "
                        f"{Path(r['dest']).name[:52]}",
                        flush=True,
                    )
                    fh.write(json.dumps({**r, "discovered": int(time.time())}) + "\n")
            problems = check_row_names(placed, journal=journal,
                                       fix=args.fix_names,
                                       series_id=args.tmdb_series)
            if problems:
                print(f"\n  {len(problems)} row-name mismatch(es) — "
                      f"{'repaired' if args.fix_names else 'see the check list'}", flush=True)
    if placed and args.refresh_lib:
        jellyfin.refresh_library(args.refresh_lib)
        # A new file lands as a 0.0-minute row until the item itself is re-read; a
        # library-wide refresh does not do it. Confirm every placed file reports a runtime.
        for r in placed:
            mins = jellyfin.ensure_row_runtime(place.to_jf(r["dest"]), args.refresh_lib)
            print(f"  row {str(mins):>6} min  {r['dest'].rsplit('/', 1)[-1][:50]}", flush=True)
        print(f"refreshed library {args.refresh_lib}", flush=True)

    print(f"\nplaced: {len(placed)}  failed: {len(results) - len(placed)}", flush=True)
    for r in results:
        if not r.get("ok"):
            print(
                f"  FAILED {Path(r['dest']).name}: {r.get('stage')} {r.get('detail', '')}",
                flush=True,
            )
    # The exit code reflects PLACEMENTS. Row discovery is informational: a row
    # that has not appeared yet must never make a successful placement look
    # failed to a caller or a monitor.
    if unverified:
        print(f"{unverified} placement(s) could not be sync-checked — see sync_error "
              f"in the journal", flush=True)
        return 2
    return 0 if len(placed) == len(results) else 1


def _fix_row_name(item_id: str, path: str, series_id: int) -> bool:
    """Name a mis-resolved row from the episode matched BY NAME on TMDB.

    Never by episode number: that is the defect being repaired. Writes only the
    fields the provider clobbered, then locks Name+Overview so the next refresh
    cannot revert it.
    """
    wanted = rowcheck.identity_from_filename(path)
    try:
        episode = tmdb.find_episode_by_name(series_id, wanted)
    except Exception as exc:  # noqa: BLE001
        print(f"      repair lookup failed: {type(exc).__name__}: {exc}", flush=True)
        return False
    if not episode or not (episode.get("overview") or "").strip():
        print(f"      no TMDB episode matched {wanted!r} — left for review", flush=True)
        return False

    uid = jellyfin.user_id()
    dto = jellyfin.get_json(f"/Users/{uid}/Items/{item_id}")
    dto.pop("Genres", None)                      # 400s the whole update
    dto.update({
        "Name": episode.get("name") or wanted,
        "Overview": episode["overview"],
        "IndexNumber": dto.get("IndexNumber"),           # include: absent = null
        "ParentIndexNumber": dto.get("ParentIndexNumber"),
    })
    if episode.get("air_date"):
        dto["PremiereDate"] = episode["air_date"]
        dto["ProductionYear"] = int(episode["air_date"][:4])
    dto["ProviderIds"] = {"Tmdb": str(episode.get("id"))}
    dto["LockedFields"] = sorted(set((dto.get("LockedFields") or []) + ["Name", "Overview"]))
    jellyfin.post_item(item_id, dto)

    back = jellyfin.get_json(f"/Users/{uid}/Items/{item_id}")
    ok = rowcheck.names_match(path, back.get("Name"))
    print(f"      {'repaired' if ok else 'STILL WRONG'}: "
          f"{back.get('Name')!r} locked={back.get('LockedFields')}", flush=True)
    return ok


def check_row_names(rows: List[Dict], *, journal: Path, fix: bool = False,
                    series_id: Optional[int] = None) -> List[Dict]:
    """Verify each placed row is named for the episode its FILE contains.

    The placement gates check the media, not the metadata: a ghost row has no
    locks, so the post-placement refresh resolves the slot by episode NUMBER and
    can hand the row a neighbouring episode's Name/Overview/ProviderIds while the
    file stays correct. Silence here is what let that go unnoticed once.
    """
    # Resolved lazily: demanding credentials to check an empty list made the
    # function unusable off this host (CI caught it), and it is the caller's
    # business whether a Jellyfin user exists yet.
    uid: Optional[str] = None
    problems: List[Dict] = []
    for r in rows:
        item_id = r.get("item_id")
        if not item_id:
            continue
        if uid is None:
            uid = jellyfin.user_id()
        dto = jellyfin.get_json(f"/Users/{uid}/Items/{item_id}",
                                params={"Fields": "Path,Name"})
        path = dto.get("Path") or r["dest"]
        name = dto.get("Name")
        if rowcheck.names_match(path, name):
            print(f"  ROW-NAME-OK       {Path(path).name[:50]}", flush=True)
            continue
        print(f"  ROW-NAME-MISMATCH {Path(path).name[:44]}", flush=True)
        print(f"      {rowcheck.describe_mismatch(path, name)}", flush=True)
        problem = {"id": item_id, "path": path, "row_name": name,
                   "expected": rowcheck.identity_from_filename(path),
                   "detail": rowcheck.describe_mismatch(path, name)}
        if fix and series_id:
            problem["repaired"] = _fix_row_name(item_id, path, series_id)
        problems.append(problem)

    if problems:
        with journal.open("a") as fh:
            for problem in problems:
                fh.write(json.dumps({"event": "row-name-mismatch", **problem}) + "\n")
        # The check list is where a human already looks; a mismatch is a
        # metadata judgement, so it goes there rather than being auto-applied
        # silently when --fix-names was not requested.
        try:
            existing = json.loads(config.CHECK_LIST.read_text()) if config.CHECK_LIST.exists() else []
            existing.extend(problems)
            safety.write_check_list(existing, config.CHECK_LIST)
        except Exception as exc:  # noqa: BLE001
            print(f"  could not write the check list: {exc}", flush=True)
    return problems


def backfill_ledger(journal: Path) -> int:
    """Record past placements in the ledger so `media_scan` stops re-offering them.

    The ledger is what `scan` subtracts candidates against. Placements made
    before the ledger was wired in are absent from it, so those releases came
    back as "new" on every scan (15 candidates for 2 genuinely new items).
    Idempotent: entries already present (by hash prefix) are left alone.
    """
    if not journal.exists():
        print("no journal to backfill", flush=True)
        return 0
    ledger = safety.load_ledger()
    added = skipped = unresolved = 0
    for line in journal.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not rec.get("ok") or not rec.get("dest"):
            continue
        if rec.get("hash"):
            h = rec["hash"]
        else:
            try:
                t = qb.torrent_for_path(rec.get("src") or "")
            except Exception:  # noqa: BLE001
                t = None
            h = (t or {}).get("hash") or ""
        if not h:
            unresolved += 1
            continue
        if safety.ledger_contains(ledger, h):
            skipped += 1
            continue
        safety.record(hash=h, action="place", dest=rec["dest"], src=rec.get("src", ""))
        ledger = safety.load_ledger()
        added += 1
    print(f"ledger backfill: added {added}, already present {skipped}, "
          f"no hash resolvable {unresolved}", flush=True)
    return added



if __name__ == "__main__":
    sys.exit(main())
