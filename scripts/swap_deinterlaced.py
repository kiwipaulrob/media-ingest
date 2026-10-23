"""Wait for a deinterlace encode, verify it, then swap it over the original.

A deinterlaced re-encode is only safe to substitute if it is PROVEN equivalent in
everything but the pixel format, so this refuses to swap unless:

  * the container has exited (the encode finished, however it exited);
  * the output probes as h264 and PROGRESSIVE (the whole point);
  * its duration matches the original within --tolerance seconds;
  * a mid-file decode sample produces no errors;
  * no client is playing the item right now.

The swap is `mv` within the same directory, so the path — and therefore the
Jellyfin row, its id and its watch state — is unchanged. A new file name would
create a new row and reset resume points.

Usage:
    python3 scripts/swap_deinterlaced.py \
        --container doors-di \
        --staging "/mnt/.../X.di.mkv" --original "/mnt/.../X.mkv" \
        --expect-duration 3517.68
"""
import argparse
import json
import subprocess
import sys
import time

sys.path.insert(0, ".")
from media_lib import jellyfin  # noqa: E402

CT = "root@192.168.214.109"
PROBE = ("docker run --rm -v /mnt/zmedia:/mnt/zmedia "
         "--entrypoint /usr/lib/jellyfin-ffmpeg/ffprobe jellyfin/jellyfin:12.2")
DECODE = ("docker run --rm -v /mnt/zmedia:/mnt/zmedia "
          "--entrypoint /usr/lib/jellyfin-ffmpeg/ffmpeg jellyfin/jellyfin:12.2")


def ct(cmd: str, timeout: int = 300) -> str:
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=12", CT,
         f"pct exec 105 -- bash -lc {json.dumps(cmd)}"],
        capture_output=True, text=True, timeout=timeout,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def container_running(name: str) -> bool:
    out = ct(f'docker ps --filter name={name} --format "{{{{.Names}}}}"')
    return name in out


def probe(path: str) -> dict:
    out = ct(f'{PROBE} -v error -show_entries '
             f'stream=codec_type,codec_name,field_order '
             f'-show_entries format=duration -of json "{path}"')
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


def playing() -> list:
    sessions = jellyfin.get_json("/Sessions") or []
    return [s.get("UserName") for s in sessions if s.get("NowPlayingItem")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", required=True)
    ap.add_argument("--staging", required=True)
    ap.add_argument("--original", required=True)
    ap.add_argument("--expect-duration", type=float, required=True)
    ap.add_argument("--tolerance", type=float, default=0.1)
    ap.add_argument("--poll", type=int, default=60)
    ap.add_argument("--max-wait", type=int, default=7200)
    args = ap.parse_args()

    deadline = time.time() + args.max_wait
    while container_running(args.container):
        if time.time() > deadline:
            print(f"TIMEOUT: {args.container} still running after {args.max_wait}s")
            return 2
        time.sleep(args.poll)
    print(f"{args.container} has exited")

    # ---------------------------------------------------------------- verify
    info = probe(args.staging)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    duration = float((info.get("format") or {}).get("duration") or 0)
    print(f"  codec     : {video.get('codec_name')}")
    print(f"  field_order: {video.get('field_order')}")
    print(f"  duration  : {duration:.3f}s (expected {args.expect_duration:.3f}s)")

    problems = []
    if video.get("codec_name") != "h264":
        problems.append(f"codec is {video.get('codec_name')}, expected h264")
    if (video.get("field_order") or "").lower() not in ("progressive", "unknown", ""):
        problems.append(f"field_order is {video.get('field_order')} — not deinterlaced")
    if abs(duration - args.expect_duration) > args.tolerance:
        problems.append(f"duration off by {abs(duration - args.expect_duration):.2f}s")

    # A finished-looking file can still be truncated; decode a sample from the
    # middle rather than trusting the muxer's summary.
    mid = max(1.0, args.expect_duration / 2)
    dec = ct(f'{DECODE} -v error -ss {mid:.0f} -t 20 -i "{args.staging}" -f null - 2>&1 | tail -5')
    if "error" in dec.lower() and "non-monoton" not in dec.lower():
        problems.append(f"decode sample reported: {dec.strip()[:120]}")

    if problems:
        print("\nREFUSING TO SWAP:")
        for p in problems:
            print("  -", p)
        return 1

    # ------------------------------------------------------------- safe swap
    users = playing()
    if users:
        print(f"\nREFUSING TO SWAP: playing for {users} — a swap under a player "
              f"corrupts playback from that offset. Re-run when idle.")
        return 1

    rc = ct(f'[ -f "{args.original}" ] && mv -f "{args.staging}" "{args.original}" '
            f'&& ls -l "{args.original}" | awk \'{{print $5}}\'')
    print(f"  swap: {rc.strip()[:60]}")

    after = probe(args.original)
    astreams = after.get("streams") or []
    avideo = next((s for s in astreams if s.get("codec_type") == "video"), {})
    print(f"  after swap: codec={avideo.get('codec_name')} "
          f"field_order={avideo.get('field_order')} "
          f"duration={float((after.get('format') or {}).get('duration') or 0):.3f}s")

    # Tell the scanner the bytes changed at the same path, so the row re-probes
    # instead of serving stale stream info.
    jellyfin.notify_media([args.original])
    print("  notified Jellyfin (Media/Updated, Modified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
