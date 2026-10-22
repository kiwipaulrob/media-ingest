"""Wait for a placed file's row, then verify and repair its metadata.

Ghost rows re-resolve from the provider BY EPISODE NUMBER on the post-placement
refresh, and this library uses scene numbering while TMDB does not — so the row can
come back carrying a neighbouring episode's name/overview/ids. Place first, then
run this: it waits for the row, checks the name against the FILE, and repairs from
the episode matched BY NAME, locking Name+Overview.

Usage:
    python3 scripts/verify_placed_row.py --path "/data/.../file.mkv" --series 9048
"""
import argparse
import sys
import time

sys.path.insert(0, ".")
from cli.ingest_new import _fix_row_name  # noqa: E402  (same repair path as the CLI)
from media_lib import awaiting, jellyfin, rowcheck  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True, help="library path (container form, /data/...)")
    ap.add_argument("--series", type=int, default=None,
                    help="TMDB series id, required to repair")
    ap.add_argument("--budget", type=int, default=420, help="seconds to wait for the row")
    args = ap.parse_args()

    print(f"waiting up to {args.budget}s for the row to appear...")
    found = awaiting.await_rows([{"dest": args.path}], budget=args.budget, interval=20)
    info = found.get(args.path, {})
    if not info.get("found"):
        print("ROW NOT YET — it can take ~9 minutes under a scan backlog; re-run this.")
        return 2

    item_id = info["item_id"]
    uid = jellyfin.user_id()
    row = jellyfin.get_json(f"/Users/{uid}/Items/{item_id}",
                            params={"Fields": "Path,Overview,LockedFields,ProviderIds"})
    name = row.get("Name")
    print(f"\nrow {item_id}")
    print(f"  Name      : {name!r}")
    print(f"  Overview  : {len(row.get('Overview') or '')}c")
    print(f"  ProviderIds: {row.get('ProviderIds')}")
    print(f"  Locked    : {row.get('LockedFields')}")

    if rowcheck.names_match(args.path, name):
        print("\nROW-NAME-OK — the row is named for the file it holds.")
        if not (row.get("LockedFields") or []):
            print("  NOTE: Name/Overview are UNLOCKED — a later refresh can revert them.")
        return 0

    print(f"\nROW-NAME-MISMATCH — {rowcheck.describe_mismatch(args.path, name)}")
    if not args.series:
        print("  pass --series <tmdb id> to repair")
        return 1
    print("  repairing from the episode matched BY NAME...")
    ok = _fix_row_name(item_id, args.path, args.series)
    after = jellyfin.get_json(f"/Users/{uid}/Items/{item_id}",
                              params={"Fields": "Name,Overview,LockedFields,ProviderIds"})
    print(f"  now: {after.get('Name')!r} "
          f"ov={len(after.get('Overview') or '')}c "
          f"locked={after.get('LockedFields')} ids={after.get('ProviderIds')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
