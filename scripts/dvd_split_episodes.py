"""Split a two-programme DVD into per-episode MKVs, deinterlaced and verified.

This release packs two Rock Family Trees episodes onto one DVD: four VOBs, ~97 min
total, and the programme boundary falls on a VOB boundary (2 VOBs each). Which half
is which was confirmed by reading a title frame from each, not guessed from the
release blurb — the blurb named both bands and the DVD presents Deep Purple first.

Encoded with the library's standing policy: software libx264 CRF 20, bwdif
deinterlace (the DVD is `field_order=tt`), audio copied untouched.

Each output is verified before it is moved into the library: codec, progressive,
a plausible duration, and a clean decode sample.
"""
import json
import subprocess
import sys

sys.path.insert(0, ".")
from media_lib import jellyfin  # noqa: E402

CT = "root@192.168.214.109"
FFMPEG = ("docker run --rm -v /mnt/zmedia:/mnt/zmedia "
          "--entrypoint /usr/lib/jellyfin-ffmpeg/ffmpeg jellyfin/jellyfin:12.2")
FFPROBE = ("docker run --rm -v /mnt/zmedia:/mnt/zmedia "
           "--entrypoint /usr/lib/jellyfin-ffmpeg/ffprobe jellyfin/jellyfin:12.2")

SRC = ("/mnt/zmedia/downloads/sabnzbd/complete/Rock Family Trees - Deep Purple &amp; "
       "Black Sabbath/Rock Family Trees - Deep Purple & Black Sabbath/Volume 1/VIDEO_TS")
LIB = "/mnt/zmedia/videos/musicdocos-series/Rock Family Trees (1995) [tmdbid-94774]"

# (label, vobs, season, episode, title, expected minutes)
JOBS = [
    ("Deep Purple", [1, 2], 1, 3, "Deep Purple People", 48.5),
    ("Black Sabbath", [3, 4], 2, 2, "Sabbath Bloody Sabbath", 48.5),
]


def ct(cmd: str, timeout: int = 3600) -> str:
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=12", CT,
         f"pct exec 105 -- bash -lc {json.dumps(cmd)}"],
        capture_output=True, text=True, timeout=timeout,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def probe(path: str) -> dict:
    out = ct(f'{FFPROBE} -v error -show_entries stream=codec_type,codec_name,field_order '
             f'-show_entries format=duration -of json "{path}"')
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


def main() -> int:
    for label, vobs, season, episode, title, expect_min in JOBS:
        staging = f"/mnt/zmedia/downloads/rft-{season:02d}{episode:02d}.mkv"
        final = f"{LIB}/Season {season:02d}/Rock Family Trees (1995) S{season:02d}E{episode:02d} {title}.mkv"

        # Concat demuxer, not the `concat:` protocol: these VOBs carry looping MPEG-2
        # timestamps (two of them report 26-hour durations), and the demuxer tolerates
        # that where the protocol produces a broken timeline.
        # The list MUST live under /mnt/zmedia: ffmpeg runs inside the jellyfin
        # container, which mounts only that tree. A /tmp path exists in the CT shell
        # but not in the container, and the encode dies with "No such file".
        listing = "/mnt/zmedia/downloads/rft-list-%d.txt" % episode
        ct(f': > {listing}; ' + "".join(
            f'echo "file \'{SRC}/VTS_01_{n}.VOB\'" >> {listing}; ' for n in vobs))

        print(f"\n=== {label}: encoding VOBs {vobs} -> S{season:02d}E{episode:02d}", flush=True)
        out = ct(f'{FFMPEG} -hide_banner -f concat -safe 0 -i {listing} '
                 f'-vf bwdif=mode=send_frame:parity=tff:deint=all '
                 f'-c:v libx264 -crf 20 -preset medium -c:a copy -map 0:a? -map 0:v '
                 f'-y "{staging}" 2>&1 | tail -3')
        print("   encode:", out.strip()[-200:], flush=True)

        info = probe(staging)
        streams = info.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        duration = float((info.get("format") or {}).get("duration") or 0)
        print(f"   probe: {video.get('codec_name')} {video.get('field_order')} "
              f"{duration/60:.2f} min", flush=True)

        problems = []
        if video.get("codec_name") != "h264":
            problems.append(f"codec {video.get('codec_name')}")
        if (video.get("field_order") or "").lower() not in ("progressive", "unknown", ""):
            problems.append(f"still {video.get('field_order')}")
        if abs(duration / 60 - expect_min) > 5:
            problems.append(f"duration {duration/60:.1f} min, expected ~{expect_min}")
        if problems:
            print("   REFUSING:", "; ".join(problems), flush=True)
            continue

        dec = ct(f'{FFMPEG} -v error -ss {int(duration/2)} -t 15 -i "{staging}" -f null - 2>&1 | tail -3')
        if "error" in dec.lower():
            print(f"   REFUSING: decode sample -> {dec.strip()[:100]}", flush=True)
            continue

        ct(f'mkdir -p "{LIB}/Season {season:02d}" && mv -f "{staging}" "{final}"')
        jellyfin.notify_media([final.replace("/mnt/zmedia/videos", "/data")])
        print(f"   PLACED {final.rsplit('/', 1)[-1]}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
