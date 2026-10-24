"""Stage 3 — apply.

Executes ONLY the explicitly approved plan rows. There is no 'all' switch on
purpose: an approval is a decision about specific items, and a bare --all would
let a stale plan act on titles nobody looked at.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_lib import config, jellyfin, qb, safety, swap


def _refresh(lib_ids, item_ids=()):
    """Re-probe swapped items, then refresh their libraries.

    The swap replaced the bytes under an EXISTING row, and a library-scoped Refresh
    re-resolves metadata for known rows — it does not re-read the media. Only a per-item
    FullRefresh picks up the new streams. The old code also issued this as a GET
    (jellyfin.get_json), while /Items/{id}/Refresh is POST-only, so it never ran at all.
    """
    for item_id in item_ids:
        try:
            jellyfin.refresh_item(item_id)
        except Exception as exc:  # noqa: BLE001 - report, never abort
            print(f"  refresh item {item_id} failed: {str(exc)[:100]}")
    for lib_id in lib_ids:
        try:
            jellyfin.refresh_library(lib_id)
        except Exception as exc:  # noqa: BLE001 - report, never abort
            print(f"  refresh {lib_id} failed: {str(exc)[:100]}")


def _lib_id(uid, name):
    rows = jellyfin.get_json("/Library/VirtualFolders")
    for r in rows if isinstance(rows, list) else []:
        if r.get("Name") == name:
            return r.get("ItemId")
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Apply approved plan rows")
    ap.add_argument(
        "--ids", required=True, help='comma-separated plan ids, e.g. 1,3,5 (no "all")'
    )
    ap.add_argument("--plan", default=str(config.STATE_DIR / "plan.json"))
    ap.add_argument(
        "--delete-source",
        action="store_true",
        help="after a verified swap, delete the torrent AND its files",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        wanted = [int(x) for x in args.ids.split(",") if x.strip()]
    except ValueError:
        print("--ids must be a comma-separated list of integers", file=sys.stderr)
        return 2
    if not wanted:
        print("refusing: no ids supplied", file=sys.stderr)
        return 2

    plan = json.loads(Path(args.plan).read_text())
    by_id = {r["id"]: r for r in plan}
    missing = [i for i in wanted if i not in by_id]
    if missing:
        print(f"refusing: ids not in plan: {missing}", file=sys.stderr)
        return 2
    rows = [by_id[i] for i in wanted]
    bad = [r["id"] for r in rows if r.get("recommend") != "REPLACE"]
    if bad:
        print(f"refusing: not REPLACE rows: {bad}", file=sys.stderr)
        return 2

    if args.dry_run:
        for r in rows:
            print(
                f"[{r['id']}] would swap {r['old_res']} -> {r['new_res']}: {r['title']}"
            )
        return 0

    with safety.acquire():
        done, failed = [], []
        for r in rows:
            print(
                f"[{r['id']}] {r['old_res']} -> {r['new_res']}  {r['title'][:50]}",
                flush=True,
            )
            try:
                status, detail = swap.replace(
                    r["src"],
                    r["dest_disk"],
                    r["old_disk"],
                    old_name=r.get("old_name") or "",
                )
            except swap.SwapError as exc:
                print(f"    FAILED: {exc}", flush=True)
                failed.append((r, str(exc)))
                continue
            # journal IMMEDIATELY: a crash must not forget a completed swap
            safety.record(
                hash=r["hash"],
                title=r.get("title"),
                status=status,
                detail=detail,
                item=r.get("old_id"),
            )
            done.append(r)
            print(f"    {status}: {detail}", flush=True)

        libs = {r.get("library") for r in done if r.get("library")}
        uid = None
        try:
            uid = jellyfin.user_id()
        except Exception:
            print("  (JELLYFIN_USER_ID unset - skipping library refresh)")
        if uid and libs:
            ids = [i for i in (_lib_id(uid, n) for n in libs) if i]
            _refresh(ids, [r["old_id"] for r in done if r.get("old_id")])

        if args.delete_source and done:
            hashes = [r["hash"] for r in done]
            qb.delete(hashes)
            after = {str(t.get("hash")) for t in qb.torrents()}
            left = [h for h in hashes if h in after]
            print(f"  deleted {len(hashes)} torrent(s); still present: {left}")

    print(f"done: {len(done)} replaced, {len(failed)} failed")
    for r, why in failed:
        print(f"  [{r['id']}] {why}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
