"""Stage 5 — place a completed download as a NEW library item.

The pipeline's `apply` stage only ever REPLACES a file that already has a row.
A genuinely new episode has no old file to retire, so it needs its own stage
rather than a special case inside the swap.

Same gate philosophy as `swap`: a completed copy is never proof of playability.
Every gate runs BEFORE the file is considered placed, and a failed gate removes
the staged copy so the library never carries a half-good file.

Nothing here guesses a destination. The caller supplies the exact path, derived
from the live library listing (the scanner keys rows off the path, so a
normalised-title guess creates a second row instead of filling the intended one).
"""
from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Optional

from . import config, probe

DECODE_SPANS = (0.05, 0.50, 0.95)


def to_disk(path: str) -> str:
    """Map a Jellyfin/container path onto the media host's disk path."""
    mapping = {**config.JF_TO_DISK, **config.SAVE_TO_REMOTE}
    for jf_prefix, disk_prefix in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        if path.startswith(jf_prefix):
            return disk_prefix + path[len(jf_prefix):]
    return path


def _frame_stats(disk_path: str) -> Dict[str, Any]:
    import json
    out = probe.on_media(probe.ffprobe_cmd(disk_path))
    data = json.loads(out or '{}')
    streams = data.get('streams') or [{}]
    fmt = data.get('format') or {}
    s = streams[0] if streams else {}
    return {'w': s.get('width'), 'h': s.get('height'), 'codec': s.get('codec_name'),
            'duration': float(fmt.get('duration') or 0),
            'title': (fmt.get('tags') or {}).get('title') or ''}


def _decodes(disk_path: str, duration: float) -> List[str]:
    """Decode short spans at start/middle/end. Returns the failures."""
    bad = []
    for frac in DECODE_SPANS:
        ss = max(0.0, duration * frac)
        cmd = (f'{probe.probe_wrap(config.FFMPEG_ENTRY)} -v error -ss {ss:.1f} '
               f'-t 5 -i {shlex.quote(disk_path)} -f null - -xerror')
        try:
            probe.on_media(cmd, timeout=180)
        except Exception as exc:                       # noqa: BLE001
            bad.append(f'{frac:.0%}:{type(exc).__name__}')
    return bad


def place_new(src: str, dest: str, *, expect_duration: Optional[float] = None,
              min_height: int = 0, max_duration_delta: float = 3.0) -> Dict[str, Any]:
    """Copy `src` to `dest` and pass every gate before calling it placed."""
    src_d = to_disk(src)
    dst_d = to_disk(dest)
    if not dst_d.lower().endswith(('.mkv', '.mp4', '.avi')):
        return {'ok': False, 'stage': 'BAD-DEST', 'dest': dest}

    src_stats = _frame_stats(src_d)
    if not src_stats['h']:
        return {'ok': False, 'stage': 'SRC-PROBE-FAIL', 'dest': dest}

    stage = f'''
set -u
SRC={shlex.quote(src_d)}; DST={shlex.quote(dst_d)}
[ -s "$SRC" ] || {{ echo STAGE=SRC-MISSING; exit 3; }}
mkdir -p "$(dirname "$DST")" || {{ echo STAGE=MKDIR-FAIL; exit 3; }}
SIZE=$(stat -c%s "$SRC")
if [ -f "$DST" ] && [ "$(stat -c%s "$DST")" = "$SIZE" ]; then
  echo COPIED=already
else
  cp --reflink=auto --sparse=always "$SRC" "$DST.part" || {{ echo STAGE=COPY-FAIL; exit 3; }}
  mv -f "$DST.part" "$DST" || {{ echo STAGE=MOVE-FAIL; exit 3; }}
  echo COPIED=fresh
fi
sync "$DST" 2>/dev/null || true
echo SIZE=$(stat -c%s "$DST")
'''
    try:
        out = probe.on_media(stage, timeout=3600)
    except Exception as exc:                            # noqa: BLE001
        return {'ok': False, 'stage': 'STAGE-EXEC-FAIL',
                'detail': f'{type(exc).__name__}: {str(exc)[:160]}', 'dest': dest}
    if 'STAGE=' in out:
        return {'ok': False, 'stage': out.split('STAGE=')[1].split()[0], 'dest': dest}

    dst_stats = _frame_stats(dst_d)
    if not dst_stats['h']:
        probe.on_media(f'rm -f {shlex.quote(dst_d)}', timeout=60)
        return {'ok': False, 'stage': 'DST-PROBE-FAIL', 'dest': dest}
    if dst_stats['h'] < min_height:
        probe.on_media(f'rm -f {shlex.quote(dst_d)}', timeout=60)
        return {'ok': False, 'stage': f"BELOW-MIN-HEIGHT({dst_stats['h']}<{min_height})",
                'dest': dest}
    if expect_duration and abs(dst_stats['duration'] - expect_duration) > max_duration_delta:
        probe.on_media(f'rm -f {shlex.quote(dst_d)}', timeout=60)
        return {'ok': False, 'stage': 'DURATION-MISMATCH',
                'detail': f"{dst_stats['duration']:.1f}s vs {expect_duration:.1f}s",
                'dest': dest}

    bad = _decodes(dst_d, dst_stats['duration'])
    if bad:
        probe.on_media(f'rm -f {shlex.quote(dst_d)}', timeout=60)
        return {'ok': False, 'stage': 'DECODE-FAIL', 'detail': ','.join(bad), 'dest': dest}

    return {'ok': True, 'stage': 'PLACED', 'dest': dest,
            'res': f"{dst_stats['w']}x{dst_stats['h']} {dst_stats['codec']}",
            'duration': round(dst_stats['duration'], 1),
            'copied': (re.search(r'COPIED=(\w+)', out) or [None, 'unknown'])[1]
            if 'COPIED=' in out else 'unknown'}
