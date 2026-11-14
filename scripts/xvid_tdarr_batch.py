"""XviD backlog -> Tdarr replacement driver (plan/queue, no direct encodes).

Replaces xvid_overnight.py's pool-host ffmpeg loop with Tdarr's pattern:
the node transcodes into its local /temp cache and the pool sees only the
single finished-file write per title. Nothing here writes to the pool;
queueing flips DB rows only.

    plan (default): audit -> video-only -> minus receipt-done -> minus
        Tdarr-job-handled -> per-library lists in Tdarr _id form, with
        live record-coverage counts. Read-only.
    queue --library LIB [--limit N] [--apply]: hand files of one library
        that already have Tdarr records to tdarr_queue (legacy flow).
        Refuses when any id is not a record.

The old script's .nfo bug is fixed structurally: video_only() admits a
fixed extension set; everything else is reported NON-VIDEO and never queued.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

VIDEO_EXT = (".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".divx", ".ogm",
             ".rmvb", ".vob", ".mpg", ".mpeg", ".wmv", ".asf", ".mov")

DONE_STATUS = ("SWAPPED", "SKIP-DONE")

DEFAULT_AUDIT = ("/root/.hermes/profiles/media-maintainer/"
                 "state/xvid-full.txt")
DEFAULT_RECEIPT = ("/root/.hermes/profiles/media-maintainer/"
                   "state/xvid-batch-receipt.jsonl")


def parse_audit(path: str | Path) -> list[str]:
    """Audit lines are 'codec,w,h,dur|Lib/relative/path'; return the paths."""
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        out.append(line.split("|", 1)[1].strip())
    return out


def video_only(paths: list[str]) -> tuple[list[str], list[str]]:
    """Split into (video, non-video) by extension. .nfo/.jpg land non-video."""
    video = [p for p in paths if p.lower().endswith(VIDEO_EXT)]
    nonvideo = [p for p in paths if p not in video]
    return video, nonvideo


def receipt_done(path: str | Path) -> set[str]:
    """Paths the old script finished (SWAPPED) or confirmed done (SKIP-DONE)."""
    done = set()
    p = Path(path)
    if not p.exists():
        return done
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("status") in DONE_STATUS and r.get("path"):
            done.add(r["path"])
    return done


def drop_job_handled(paths: list[str], job_files: list[str]) -> list[str]:
    """Drop backlog paths Tdarr already handled (match on basename)."""
    handled = {f.rsplit("/", 1)[-1] for f in job_files if f}
    return [p for p in paths if p.rsplit("/", 1)[-1] not in handled]


def to_tdarr_id(rel: str) -> str:
    """Audit paths are relative to the pool videos root; Tdarr ids are not."""
    return "/media/videos/" + rel


def group_by_library(ids: list[str]) -> dict[str, list[str]]:
    """Group Tdarr _ids by first path segment (the library folder)."""
    groups: dict[str, list[str]] = {}
    for i in ids:
        lib = i[len("/media/videos/"):].split("/", 1)[0]
        groups.setdefault(lib, []).append(i)
    return groups


def live_records() -> set[str]:
    """Tdarr FileJSONDB _ids. Deferred import: needs TDARR_API_KEY."""
    from tdarr_kick import crud as _crud  # noqa: E402

    docs = _crud("FileJSONDB", "getAll")
    docs = docs if isinstance(docs, list) else list(docs.values())
    return {str(d.get("_id")) for d in docs if isinstance(d, dict)}


def live_success_files() -> list[str]:
    """Files with a 'Transcode success' job record. Deferred import."""
    from tdarr_kick import crud as _crud  # noqa: E402

    jobs = _crud("JobsJSONDB", "getAll")
    jobs = jobs if isinstance(jobs, list) else list(jobs.values())
    return [j.get("file") for j in jobs
            if isinstance(j, dict) and j.get("status") == "Transcode success"
            and isinstance(j.get("file"), str)]


def build_plan(audit: str, receipt: str,
               job_files: list[str] | None) -> dict:
    """Pure plan core: everything except the live record-coverage check."""
    paths = [p for p in parse_audit(audit) if p]
    video, nonvideo = video_only(paths)
    fresh = [p for p in video if p not in receipt_done(receipt)]
    if job_files is not None:
        fresh = drop_job_handled(fresh, job_files)
    groups = group_by_library([to_tdarr_id(p) for p in fresh])
    return {"audit": len(paths), "nonvideo": nonvideo,
            "remaining": len(fresh), "groups": groups}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="XviD backlog -> Tdarr driver.")
    ap.add_argument("--audit", default=DEFAULT_AUDIT)
    ap.add_argument("--receipt", default=DEFAULT_RECEIPT)
    ap.add_argument("--library",
                    help="queue mode: exactly one library folder")
    ap.add_argument("--limit", type=int, default=20,
                    help="queue mode: max files to hand over (default 20)")
    ap.add_argument("--skip-unrecorded", action="store_true",
                    help="queue mode: queue only record-covered files, warning "
                    "about the rest (default refuses when any id lacks a record)")
    ap.add_argument("--apply", action="store_true",
                    help="queue mode: actually flip the rows (default dry run)")
    a = ap.parse_args(argv)

    try:
        jobs = live_success_files()
    except Exception as exc:  # noqa: BLE001 - plan must never die on API
        print(f"  Tdarr job history unreadable ({type(exc).__name__}) — "
              "treating all as unhandled")
        jobs = None
    plan = build_plan(a.audit, a.receipt, jobs)
    print(f"audit lines: {plan['audit']}  non-video excluded: "
          f"{len(plan['nonvideo'])}  remaining: {plan['remaining']}")
    for nv in plan["nonvideo"][:10]:
        print(f"  NON-VIDEO {nv}")

    try:
        known = live_records()
    except Exception as exc:  # noqa: BLE001 - coverage is advisory
        print(f"  Tdarr records unreadable ({type(exc).__name__}) — "
              "coverage unknown")
        known = set()

    for lib in sorted(plan["groups"]):
        ids = plan["groups"][lib]
        have = sum(1 for i in ids if i in known) if known else -1
        cov = f"{have}/{len(ids)} records" if known else "coverage unknown"
        print(f"  {lib}: {len(ids)} file(s), {cov}")
        if not a.library:
            for i in ids[:5]:
                print(f"    {i}")
            if len(ids) > 5:
                print(f"    ... +{len(ids) - 5} more")

    if not a.library:
        print("plan only — pass --library <name> [--limit N] [--apply] "
              "to queue one library")
        return 0

    if a.library not in plan["groups"]:
        print(f"  refusing: no remaining files under {a.library!r}")
        return 2
    ids = [i for i in plan["groups"][a.library] if i in known]
    missing = len(plan["groups"][a.library]) - len(ids)
    if missing and not a.skip_unrecorded:
        print(f"  refusing: {missing} file(s) have no "
              "Tdarr record — scan that library folder first, one at a time "
              "(or pass --skip-unrecorded to queue the rest)")
        return 2
    if missing:
        print(f"  skipping {missing} file(s) with no Tdarr record")
    batch = ids[:a.limit]
    import tdarr_queue  # noqa: E402

    args = ["--flow", "legacy", "--files", *batch]
    if a.apply:
        args.append("--apply")
    return int(tdarr_queue.main(args))


if __name__ == "__main__":
    raise SystemExit(main())
