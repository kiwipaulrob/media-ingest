"""Container/poison detection and ffprobe through the media host.

Execution model (verified 2026-10-20):
  * both the library and the downloads live behind CT105 on prox2
  * neither prox2 nor CT105 has ffprobe installed
  * the jellyfin image carries /usr/lib/jellyfin-ffmpeg/{ffprobe,ffmpeg}

So a probe is a throwaway container with /mnt/zmedia bind-mounted. That is
deliberately preferable to installing ffmpeg on the host or adding a bind to the
live compose file: it changes nothing persistent and uses the exact binary the
server itself uses.

The magic-byte check exists because a complete torrent can deliver nothing but
zeros: 100% progress plus a full byte count is not proof the bytes are valid.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import Dict, List, Optional, Tuple, TypedDict

from . import config


def media_cmd(inner: str) -> str:
    """Wrap a shell command so it runs inside CT105 on the media host."""
    return f"{config.MEDIA_CT} sh -c {shlex.quote(inner)}"


def on_media(inner: str, timeout: int = 300) -> str:
    """Run `inner` inside CT105 and return stdout. Raises on non-zero exit."""
    proc = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            config.MEDIA_HOST,
            media_cmd(inner),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"media command failed ({proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[:300]}"
        )
    return proc.stdout


def query(inner: str, timeout: int = 300) -> Tuple[int, str]:
    """Run a QUESTION inside CT105 and return (exit_code, stdout).

    on_media() raises on a non-zero exit, which is correct for an ACTION (a failed copy or
    move must not pass silently) and wrong for a QUESTION: `[ -d X ]` and `grep -c` return 1
    to mean "no", and a missing file makes `ls` exit 2. Using the action call for those
    turned a normal answer into a crash — `[ -d X ] && echo yes` aborted the completeness
    gate on a path that simply was not a DVD folder.

    Never raises: a timeout or an unreachable host comes back as (-1, "").
    """
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=15",
                config.MEDIA_HOST,
                media_cmd(inner),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return -1, ""
    except OSError:
        return -1, ""
    return proc.returncode, proc.stdout


def probe_wrap(binary: str) -> str:
    """Shell prefix that runs an image binary with the media volume mounted."""
    return (
        f"docker run --rm -v {config.MEDIA_VOLUME}:{config.MEDIA_VOLUME} "
        f"--entrypoint {binary} {config.media_image()}"
    )


def ffprobe_cmd(path: str) -> str:
    return (
        f"{probe_wrap(config.FFPROBE_ENTRY)} -v error "
        f"-show_entries stream=index,codec_type,width,height,codec_name,channels,sample_aspect_ratio,display_aspect_ratio:stream_tags=language:format=duration,size:format_tags=title,description,synopsis,comment,date "  # noqa: E501 - Review-P3.5a tags
        f"-of json {shlex.quote(path)}"
    )


def ffmpeg_cmd(args: str, path: str, extra: str = "") -> str:
    return f"{probe_wrap(config.FFMPEG_ENTRY)} -v error {args} -i {shlex.quote(path)} {extra}"


MKV_MAGIC = bytes.fromhex("1a45dfa3")


def container_from_head(head: bytes) -> str:
    """Classify the first bytes of a file. Pure function, no I/O."""
    if not head:
        return "unknown"
    if head[:4] == MKV_MAGIC:
        return "matroska"  # mkv and webm share this signature
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "mp4"
    if head[0:1] == b"\x47":
        return "mpegts"
    if len(head) >= 16 and head[:64].count(b"\x00") == len(head[:64]):
        return "poisoned"
    return "unknown"


def extension_for_container(container: str) -> Optional[str]:
    """The extension a file must carry for this container, or None if unknown."""
    return {"matroska": ".mkv", "avi": ".avi", "mp4": ".mp4", "mpegts": ".ts"}.get(
        container
    )


def head_bytes(remote_path: str, n: int = 64) -> bytes:
    """First n bytes of a media-host file (poison/swarm check)."""
    out = on_media(
        f"head -c {int(n)} {shlex.quote(remote_path)} | od -An -tx1 -v", timeout=120
    )
    return bytes.fromhex("".join(out.split()))[:n]


class ProbeResult(TypedDict):
    """The ONLY shape probe.probe() returns.

    Declared because a consumer read it as raw ffprobe JSON (info["format"]["duration"]) and
    every flat file was silently refused as "duration unreadable". With this type, that
    lookup is a static error instead of a production bug.

    Every key is ALWAYS present; the VALUES are what may be unknown (a stream can report no
    width or duration). total=False would have said the keys may be missing, which is not
    what the function does and would let a caller skip the presence check it does not need.
    """
    w: Optional[int]
    h: Optional[int]
    codec: Optional[str]
    dur: Optional[float]
    size: int
    title: Optional[str]
    # Review-P3.5a: embedded container tags (BBC iPlayer synopsis, broadcast
    # date) — an offline overview when external providers have nothing.
    synopsis: Optional[str]
    aired: Optional[str]
    # Aspect flags straight from the video stream (aspect audit): a wrong SAR
    # plays squeezed while the pixels are fine — "5:4", "16:9", or None.
    sar: Optional[str]
    dar: Optional[str]
    # Audio track list for the language/bloat advisory (Moana rule): each
    # {"codec","channels","lang"}. [] when the probe predates audio select.
    audio: List[Dict]


def probe(remote_path: str) -> ProbeResult:
    """ffprobe one file via the media host. Returns {} on failure.

    A missing 'h' must be treated as PROBE-FAIL by callers — never guessed.
    """
    try:
        data = json.loads(on_media(ffprobe_cmd(remote_path), timeout=180) or "{}")
    except Exception:
        # A failed probe returns the SHAPE with unknown values, not {} — an empty dict
        # violates the declared contract and made "could not measure" indistinguishable
        # from a type that was never meant to carry the key.
        return {"w": None, "h": None, "codec": None, "dur": None, "size": 0, "title": None,
                "synopsis": None, "aired": None, "sar": None, "dar": None, "audio": []}
    streams = data.get("streams") or [{}]
    fmt = data.get("format") or {}
    video = next((x for x in streams
                  if isinstance(x, dict) and x.get("codec_type") == "video"),
                 streams[0] if streams else {})
    s = video
    audio = [{"codec": a.get("codec_name"),
              "channels": a.get("channels"),
              "lang": ((a.get("tags") or {}).get("language") or "").lower() or None}
             for a in streams
             if isinstance(a, dict) and a.get("codec_type") == "audio"]
    dur = fmt.get("duration")
    try:
        dur = float(dur) if dur not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        dur = None
    try:
        size = int(fmt.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    tags = fmt.get("tags") or {}
    # First non-empty embedded synopsis-ish tag (Review-P3.5a).
    synopsis = next(
        (str(tags.get(k) or "").strip()
         for k in ("description", "synopsis", "comment")
         if str(tags.get(k) or "").strip()),
        None,
    )
    return {
        "w": s.get("width"),
        "h": s.get("height"),
        "codec": s.get("codec_name"),
        "dur": dur,
        "size": size,
        "title": tags.get("title"),
        "synopsis": synopsis,
        "aired": str(tags.get("date") or "").strip() or None,
        "sar": s.get("sample_aspect_ratio"),
        "dar": s.get("display_aspect_ratio"),
        "audio": audio,
    }


def decode_check(remote_path: str, seconds: int = 8) -> bool:
    """Decode a short span mid-file. A completed copy is not proof of playability."""
    cmd = (
        ffmpeg_cmd("-ss 60 -t %d -f null -" % int(seconds), remote_path)
        + " 2>&1 | head -5"
    )
    # A decode PROBE, so a non-zero exit is expected: ffmpeg exits non-zero after printing
    # elevated warnings it recovered from. Using on_media() here turned those into "corrupt".
    _rc, out = query(cmd, timeout=600)
    if _rc == -1:
        return False
    # Some broadcast h264 streams emit "mmco: unref short failure" on every seek — a
    # reference-frame warning from the encoder, not damage. It arrived at -v error level, so
    # the old `not out.strip()` test marked a perfectly decodable 1080i HDTV capture as
    # corrupt. Judge on what is left after removing known-benign decoder chatter.
    benign = ("mmco:", "unref short", "last message repeated", "concealing ",
              "reference picture missing", "missing reference picture")
    remaining = [ln for ln in out.splitlines()
                 if not any(b in ln.lower() for b in benign)]
    return not "".join(remaining).strip()
