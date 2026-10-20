"""Container/poison detection and remote ffprobe.

The magic-byte check exists because a complete torrent can deliver nothing but
zeros: 100% progress plus a full byte count is not proof the bytes are valid. A
poisoned file must never be ingested, and a staged copy of it must be swept out
of the library folder.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any, Dict, Optional

from . import config

MKV_MAGIC = bytes.fromhex('1a45dfa3')
RIFF = b'RIFF'
AVI = b'AVI '


def container_from_head(head: bytes) -> str:
    """Classify the first bytes of a file. Pure function, no I/O."""
    if not head:
        return 'unknown'
    if head[:4] == MKV_MAGIC:
        return 'matroska'          # mkv and webm share this signature
    if len(head) >= 12 and head[:4] == RIFF and head[8:12] == AVI:
        return 'avi'
    if len(head) >= 12 and head[4:8] == b'ftyp':
        return 'mp4'
    if head[0:1] == b'\x47':
        return 'mpegts'
    if head.count(b'\x00') == len(head[:64]) and len(head) >= 16:
        return 'poisoned'          # all-zero head: payload is garbage
    return 'unknown'


def extension_for_container(container: str) -> Optional[str]:
    """The extension a file must carry for this container, or None if unknown."""
    return {
        'matroska': '.mkv', 'avi': '.avi', 'mp4': '.mp4', 'mpegts': '.ts',
    }.get(container)


def _ssh(cmd: str, timeout: int = 300) -> str:
    """Run a command on the media host. All media paths are only valid there."""
    proc = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
         config.PROXMOX3, cmd],
        capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f'ssh failed ({proc.returncode}): {proc.stderr.strip()[:300]}')
    return proc.stdout


def head_bytes(remote_path: str, n: int = 64) -> bytes:
    """First n bytes of a remote file (poison/swarm check)."""
    out = _ssh(f"head -c {int(n)} {shlex.quote(remote_path)} | od -An -tx1 -v")
    raw = bytes.fromhex(''.join(out.split()))
    return raw[:n]


def probe(remote_path: str) -> Dict[str, Any]:
    """ffprobe one remote file for width/height/codec/duration.

    Returns {} on failure; callers must treat a missing 'h' as PROBE-FAIL rather
    than guessing. In-container ffmpeg is NOT on PATH — the absolute path is
    load-bearing.
    """
    cmd = (
        f"ffprobe -v error -select_streams v:0 "
        f"-show_entries stream=width,height,codec_name:format=duration:format_tags=title "
        f"-of json {shlex.quote(remote_path)}"
    )
    try:
        data = json.loads(_ssh(cmd, timeout=120) or '{}')
    except Exception:
        return {}
    streams = data.get('streams') or [{}]
    fmt_block = data.get('format') or {}
    s = streams[0] if streams else {}
    dur = fmt_block.get('duration')
    return {
        'w': s.get('width'), 'h': s.get('height'), 'codec': s.get('codec_name'),
        'dur': float(dur) if dur not in (None, '', 'N/A') else None,
        'title': (fmt_block.get('tags') or {}).get('title'),
    }


def decode_check(remote_path: str, at: float = 0.5, seconds: int = 10) -> bool:
    """Decode a short span mid-file. A completed copy is not proof of playability."""
    cmd = (
        f"ffmpeg -v error -ss {max(0.0, float(at)):.1f} -i {shlex.quote(remote_path)} "
        f"-t {int(seconds)} -f null - 2>&1 | head -5"
    )
    try:
        out = _ssh(cmd, timeout=600)
    except Exception:
        return False
    return not out.strip()
