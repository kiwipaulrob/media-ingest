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
from typing import Any, Dict, Optional

from . import config


def media_cmd(inner: str) -> str:
    """Wrap a shell command so it runs inside CT105 on the media host."""
    return f'{config.MEDIA_CT} sh -c {shlex.quote(inner)}'


def on_media(inner: str, timeout: int = 300) -> str:
    """Run `inner` inside CT105 and return stdout. Raises on non-zero exit."""
    proc = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
         config.MEDIA_HOST, media_cmd(inner)],
        capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f'media command failed ({proc.returncode}): '
            f'{(proc.stderr or proc.stdout).strip()[:300]}')
    return proc.stdout


def probe_wrap(binary: str) -> str:
    """Shell prefix that runs an image binary with the media volume mounted."""
    return (f'docker run --rm -v {config.MEDIA_VOLUME}:{config.MEDIA_VOLUME} '
            f'--entrypoint {binary} {config.media_image()}')


def ffprobe_cmd(path: str) -> str:
    return (f'{probe_wrap(config.FFPROBE_ENTRY)} -v error -select_streams v:0 '
            f'-show_entries stream=width,height,codec_name:format=duration:format_tags=title '
            f'-of json {shlex.quote(path)}')


def ffmpeg_cmd(args: str, path: str, extra: str = '') -> str:
    return f'{probe_wrap(config.FFMPEG_ENTRY)} -v error {args} -i {shlex.quote(path)} {extra}'


MKV_MAGIC = bytes.fromhex('1a45dfa3')


def container_from_head(head: bytes) -> str:
    """Classify the first bytes of a file. Pure function, no I/O."""
    if not head:
        return 'unknown'
    if head[:4] == MKV_MAGIC:
        return 'matroska'            # mkv and webm share this signature
    if len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == b'AVI ':
        return 'avi'
    if len(head) >= 12 and head[4:8] == b'ftyp':
        return 'mp4'
    if head[0:1] == b'\x47':
        return 'mpegts'
    if len(head) >= 16 and head[:64].count(b'\x00') == len(head[:64]):
        return 'poisoned'
    return 'unknown'


def extension_for_container(container: str) -> Optional[str]:
    """The extension a file must carry for this container, or None if unknown."""
    return {'matroska': '.mkv', 'avi': '.avi', 'mp4': '.mp4',
            'mpegts': '.ts'}.get(container)


def head_bytes(remote_path: str, n: int = 64) -> bytes:
    """First n bytes of a media-host file (poison/swarm check)."""
    out = on_media(f'head -c {int(n)} {shlex.quote(remote_path)} | od -An -tx1 -v',
                   timeout=120)
    return bytes.fromhex(''.join(out.split()))[:n]


def probe(remote_path: str) -> Dict[str, Any]:
    """ffprobe one file via the media host. Returns {} on failure.

    A missing 'h' must be treated as PROBE-FAIL by callers — never guessed.
    """
    try:
        data = json.loads(on_media(ffprobe_cmd(remote_path), timeout=180) or '{}')
    except Exception:
        return {}
    streams = data.get('streams') or [{}]
    fmt = data.get('format') or {}
    s = streams[0] if streams else {}
    dur = fmt.get('duration')
    try:
        dur = float(dur) if dur not in (None, '', 'N/A') else None
    except (TypeError, ValueError):
        dur = None
    return {
        'w': s.get('width'),
        'h': s.get('height'),
        'codec': s.get('codec_name'),
        'dur': dur,
        'title': (fmt.get('tags') or {}).get('title'),
    }


def decode_check(remote_path: str, seconds: int = 8) -> bool:
    """Decode a short span mid-file. A completed copy is not proof of playability."""
    cmd = (ffmpeg_cmd('-ss 60 -t %d -f null -' % int(seconds), remote_path)
           + ' 2>&1 | head -5')
    try:
        out = on_media(cmd, timeout=600)
    except Exception:
        return False
    return not out.strip()
