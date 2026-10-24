#!/usr/bin/env python3
"""Ingest completed Jools Holland downloads into the series, then clear the payload.

Idempotent: safe to run repeatedly (hourly cron). For each COMPLETE torrent in the chosen
seasons it verifies the video, moves it into Season NN using the plain 'SxxEyy' filename
form Jellyfin parses, refreshes the row, and removes the torrent plus its leftover files.

Never touches an incomplete torrent (partials are resumable progress).
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from media_lib import config  # noqa: E402

from media_lib import avsync, jellyfin, qb  # noqa: E402

NODE = config.NODE
SERIES = "/media/videos/musicdocos-series/Later with Jools Holland (1992) [tmdbid-10959]"
TITLE = "Later with Jools Holland (1992)"
SEASONS = {49, 50, 66, 67, 68, 69}
SE = re.compile(r"[Ss](\d{1,2})\s*[Ee](\d{1,3})")
VIDEO_EXT = (".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".divx", ".ogm", ".rmvb", ".vob")
# Broadcast rips arrive as MPEG-TS; a .mkv/.mp4-only filter reports them as
# "no video file" and leaves a complete episode queued forever.


def run(inner, timeout=600):
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", NODE,
         f"docker exec tdarr-node sh -c {shlex.quote(inner)}"],
        capture_output=True, text=True, timeout=timeout)


def main(apply: bool = True) -> int:
    torrents = qb.torrents() or []
    done = []
    for t in torrents:
        m = SE.search(str(t.get("name", "")))
        if not m:
            continue
        season, ep = int(m.group(1)), int(m.group(2))
        if season not in SEASONS or (t.get("progress") or 0) < 1.0:
            continue
        done.append((season, ep, t))

    print(f"{len(done)} complete episode torrent(s) in scope")
    ingested = 0
    sync_problems = []
    for season, ep, t in sorted(done):
        label = f"S{season:02d}E{ep:02d}"
        dest_dir = f"{SERIES}/Season {season:02d}"
        # already placed?
        found = run(f"ls -1 {shlex.quote(dest_dir)} 2>/dev/null | "
                    f"grep -i 'S{season:02d}E{ep:02d}' | head -1")
        if (found.stdout or "").strip():
            print(f"  {label} already in library — clearing payload")
            if apply:
                qb.delete([t["hash"]], delete_files=True)
            continue

        files = [f for f in (qb.files(t["hash"]) or [])
                 if str(f.get("name", "")).lower().endswith(VIDEO_EXT)
                 and (f.get("size") or 0) > 100_000_000]
        if not files:
            print(f"  {label} no video file found")
            continue
        src = f"/media/downloads/{files[0]['name']}"
        probe = run(f"ffprobe -v error -show_entries format=duration -of csv=p=0 {shlex.quote(src)}")
        try:
            dur = float((probe.stdout or "0").strip() or 0)
        except ValueError:
            dur = 0
        if dur < 300:
            print(f"  {label} duration {dur:.0f}s — not a full episode, skipping")
            continue

        dest = f"{dest_dir}/{TITLE} S{season:02d}E{ep:02d}.mkv"
        print(f"  {label} {dur/60:.1f} min -> {dest.split('/')[-1]}")
        if not apply:
            continue
        run(f"mkdir -p {shlex.quote(dest_dir)}")
        if src.lower().endswith(".mkv"):
            mv = run(f"mv -n {shlex.quote(src)} {shlex.quote(dest)}")
        else:
            # Stream-copy into matroska: a .ts renamed to .mkv is a container lie, and a
            # broadcast TS carries teletext we do not want in the library.
            # ffmpeg can stop early on a broadcast TS (dead air / discontinuity), and a
            # truncated remux looks like a successful move — so the OUTPUT DURATION is
            # verified against the source below before anything is accepted or deleted.
            tmp = dest + ".part.mkv"
            # PLAIN copy first: +genpts/avoid_negative_ts on a broadcast TS with a PTS
            # discontinuity makes ffmpeg stop a few seconds in, which once produced a
            # 4-second "episode" that the old presence-only check accepted.
            attempts = [
                "",
                "-fflags +genpts -avoid_negative_ts make_zero ",
            ]
            got_dur = 0
            mv = None
            for extra in attempts:
                run(f"rm -f {shlex.quote(tmp)}")
                mv = run(f"ffmpeg -v error -y {extra}-i {shlex.quote(src)} "
                         f"-map 0:v:0 -map 0:a? -c copy -f matroska "
                         f"{shlex.quote(tmp)} 2>&1 | tail -5", timeout=3600)
                got = run(f"ffprobe -v error -show_entries format=duration -of csv=p=0 "
                          f"{shlex.quote(tmp)} 2>/dev/null")
                try:
                    got_dur = float((got.stdout or "0").strip() or 0)
                except ValueError:
                    got_dur = 0
                if got_dur >= dur * 0.95:
                    break
                print(f"    remux short ({got_dur:.0f}s of {dur:.0f}s){' retrying with genpts' if not extra else ''}")
            if got_dur < dur * 0.95:
                # ffmpeg's matroska muxer REFUSES packets with no PTS ("Can't write packet
                # with unknown timestamp") and aborts at the first one. On these NX broadcast
                # TS rips ~7 video packets/s from ~3.6 s onward carry N/A timestamps, so
                # both `-c copy` attempts above "finish" with a 4-second file. mkvmerge
                # derives timestamps from the track and carried a full 3656 s programme in
                # 88 s, video+audio, verified decodable at 30:00 — so it is the fallback,
                # not a re-encode.
                print("    ffmpeg copy aborted on unstamped packets — retrying with mkvmerge")
                run(f"rm -f {shlex.quote(tmp)}")
                mv = run(f"mkvmerge -o {shlex.quote(tmp)} -s 0 --no-chapters "
                         f"--disable-track-statistics-tags {shlex.quote(src)} 2>&1 | tail -5",
                         timeout=3600)
                got = run(f"ffprobe -v error -show_entries format=duration -of csv=p=0 "
                          f"{shlex.quote(tmp)} 2>/dev/null")
                try:
                    got_dur = float((got.stdout or "0").strip() or 0)
                except ValueError:
                    got_dur = 0
            if got_dur < dur * 0.95:
                print(f"    remux failed all ways: {(mv.stdout or '').strip()[-160:]}")
                run(f"rm -f {shlex.quote(tmp)}")
                print("    keeping payload — NOT deleting, retry next run")
                continue
            mv = run(f"mv -f {shlex.quote(tmp)} {shlex.quote(dest)}")
        present = run(f"test -f {shlex.quote(dest)} && echo PRESENT || echo MISSING")
        if "PRESENT" not in (present.stdout or ""):
            print(f"    move failed: {(mv.stderr or '').strip()[:80]}")
            continue
        ingested += 1

        # A/V SYNCH GATE: a DVD/VOB-sourced file can carry an audio timeline that runs
        # ~1 s longer than the picture (broken MPEG timestamps). The library copy is the
        # last chance to notice — once the payload is deleted there is nothing to re-mux.
        sync_path = dest.replace("/media/", "/mnt/zmedia/", 1)
        sync = avsync.check(sync_path)
        if sync.get("verdict") == "fail":
            # Try to fix it in place first. An offset or an over-long audio track is
            # repairable by stream-copying the picture, so the video is never degraded
            # and the payload can still be released.
            fixed_to = sync_path + ".resync.mkv"
            fix = avsync.resync(sync_path, fixed_to)
            if fix.get("ok"):
                run(f"mv -f {shlex.quote(fixed_to.replace('/mnt/zmedia/', '/media/', 1))} "
                    f"{shlex.quote(dest)}")
                print(f"    sync REPAIRED ({fix.get('method')}) — {sync.get('problems')}")
                sync = avsync.check(sync_path)
            if sync.get("verdict") == "fail":
                print(f"    SYNC UNREPAIRABLE ({fix.get('reason')}) — keeping payload: "
                      f"{sync.get('problems')}")
                sync_problems.append((label, sync.get("problems")))
                continue
        print(f"    sync: {sync.get('verdict')} "
              f"(video {sync.get('video_duration')}s, derived={sync.get('durations_derived')})")
        # leftover sidecars are a duplicate of data now in the library
        qb.delete([t["hash"]], delete_files=True)

    if apply and ingested:
        print(f"\ningested {ingested}; refreshing library")
        jellyfin.refresh_library("18ce65a0ab9d7a771d5ac55e6024fe2a")
        time.sleep(30)
        report_orphans()
    if sync_problems:
        print(f"\nWARNING {len(sync_problems)} episode(s) failed the A/V sync gate and kept "
              f"their payload:")
        for label, probs in sync_problems:
            print(f"  {label}: {'; '.join(probs)}")
    return 0


def report_orphans() -> None:
    """Flag episodes Jellyfin could not attach to a season.

    A row can come back with ParentIndexNumber/IndexNumber null right after a file is added.
    That is usually TRANSIENT: S66E03 sat unnumbered through several checks and then attached
    itself as S66E03 once the scan/refresh settled. Do not conclude "the provider has no such
    season" from a few minutes of nulls — verify against the server source first.

    If it genuinely stays null, POST /Items/{itemId} DOES accept IndexNumber and
    ParentIndexNumber (ItemUpdateController.UpdateItem assigns both; the endpoint requires
    elevation). The web UI also exposes the field (#txtIndexNumber) for episodes.
    """
    uid = jellyfin.user_id()
    r = jellyfin.get_json(f"/Users/{uid}/Items", params={
        "searchTerm": "Jools", "IncludeItemTypes": "Series", "Recursive": "true", "Limit": "3"})
    sid = (r.get("Items") or [])[0]["Id"]
    eps = jellyfin.get_json(f"/Users/{uid}/Items", params={
        "ParentId": sid, "Recursive": "true", "IncludeItemTypes": "Episode",
        "Fields": "ParentIndexNumber,IndexNumber,Path", "Limit": "400"})
    orphans = [e for e in (eps.get("Items") or []) if e.get("ParentIndexNumber") is None]
    if not orphans:
        print("all episodes numbered")
        return
    print(f"WARNING {len(orphans)} episode(s) unnumbered (provider has no such season):")
    for e in orphans[:10]:
        print("   ", (e.get("Path") or "").split("/")[-1][:60])


if __name__ == "__main__":
    raise SystemExit(main(apply="--dry-run" not in sys.argv))
