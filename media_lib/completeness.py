"""Is the SOURCE complete? Gate this before placing or recoding anything.

`place.py` already gates SIZE, PROBE and DECODE on the copy it makes — but those all
inspect the file that was copied, so a source that is *missing parts* passes every one
of them. Two real cases from this library (24 Oct 2026):

* **A hole in the VOB chain.** Gil Scott-Heron has `VTS_01_1, _2, _4` — 3.0 GB, 44.5 min
  against a 62 min film. Joining that chain silently drops ~17 minutes.
* **Stalled MPEG-2 timestamps.** Moog's VOB reports 55 s and Put More Blood's 196 s while
  the data is complete; every seek past the reported duration returns the same frame.
  Placing or encoding from those durations bakes the error in.

Everything here answers "does this source hold the whole programme?", which no
per-file probe can tell you on its own.
"""
from __future__ import annotations

import re
import shlex
from typing import List

from . import probe

DVD_MAX_MBPS = 12.0        # DVD spec ceiling is 10.08; margin for container overhead
MIN_MAIN_BYTES = 300_000_000
_VOB_RE = re.compile(r"VTS_(\d+)_(\d+)\.VOB", re.IGNORECASE)


def _remote(script: str, timeout: int = 900) -> str:
    return probe.on_media(script, timeout=timeout)


def vob_chain_problems(video_ts_dir: str) -> List[str]:
    """Gaps in the VOB numbering, and VOBs whose duration/bitrate are impossible."""
    d = shlex.quote(video_ts_dir)
    ffp = probe.probe_wrap(probe_ffprobe_entry())
    # One round-trip: emit "name size duration" per main VOB.
    script = (
        f'ls -1 {d} 2>/dev/null | while read -r n; do '
        f'case "$n" in *.VOB|*.vob) ;; *) continue ;; esac; '
        f'sz=$(stat -c %s {d}/"$n" 2>/dev/null); [ -z "$sz" ] && continue; '
        f'[ "$sz" -lt {MIN_MAIN_BYTES} ] && continue; '
        f'dur=$({ffp} -v error -show_entries format=duration -of csv=p=0 {d}/"$n" 2>/dev/null | head -1); '
        f'echo "$n $sz $dur"; done'
    )
    rows: List[tuple[str, int, float]] = []
    for line in _remote(script).splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            rows.append((parts[0], int(parts[1]), float(parts[2])))
        except ValueError:
            rows.append((parts[0], int(parts[1]), 0.0))

    problems: List[str] = []

    sets: dict[str, List[int]] = {}
    for name, _sz, _dur in rows:
        m = _VOB_RE.fullmatch(name)
        if m:
            sets.setdefault(m.group(1), []).append(int(m.group(2)))
    for tset, idx in sets.items():
        have = sorted(i for i in idx if i >= 1)
        if have and have != list(range(1, len(have) + 1)):
            missing = [i for i in range(1, max(have) + 1) if i not in have]
            problems.append(
                f"VOB chain incomplete: VTS_{tset} has {have}, missing {missing} — "
                f"the programme is truncated; do not place or join it")

    for name, sz, dur in rows:
        if dur <= 0:
            problems.append(f"{name}: duration unreadable")
            continue
        mbps = sz * 8 / dur / 1_000_000
        if mbps > DVD_MAX_MBPS:
            problems.append(
                f"{name}: {mbps:.1f} Mbps over {dur:.0f}s — stalled timestamps "
                f"(data present, PTS broken); regenerate with setpts=N/FRAME_RATE/TB")
    return problems


def probe_ffprobe_entry() -> str:
    """The ffprobe binary name for probe.probe_wrap — kept in one place."""
    from . import config
    return config.FFPROBE_ENTRY


def source_problems(path: str, *, max_duration_delta: float | None = None,
                    expect_duration: float | None = None) -> List[str]:
    """Completeness problems for a source, whether a DVD folder or a flat file."""
    listing = _remote(f'ls -1 {shlex.quote(path)} 2>/dev/null | head -5')
    is_dir = not listing.strip() or any(
        line.strip() in ("VIDEO_TS", "video_ts") for line in listing.splitlines())

    if is_dir:
        vts = f"{path.rstrip('/')}/VIDEO_TS"
        if not _remote(f'[ -d {shlex.quote(vts)} ] && echo yes').strip().endswith("yes"):
            vts = path.rstrip("/")
        problems = vob_chain_problems(vts)
        if expect_duration:
            problems += _duration_gap(problems, vts, expect_duration)
        return problems

    # Flat file: a stalled-timestamp container shows the same impossible bitrate.
    info = probe.probe(path)
    fmt = (info or {}).get("format") or {}
    dur = float(fmt.get("duration") or 0)
    size = int(fmt.get("size") or 0)
    problems: List[str] = []
    if dur <= 0:
        problems.append("duration unreadable — cannot confirm the file is complete")
    elif size and path.lower().endswith((".vob", ".mpg", ".mpeg")):
        mbps = size * 8 / dur / 1_000_000
        if mbps > DVD_MAX_MBPS:
            problems.append(f"{mbps:.1f} Mbps over {dur:.0f}s — stalled timestamps")
    if expect_duration and dur > 0:
        delta = abs(dur - expect_duration)
        if delta > (max_duration_delta or 3.0):
            problems.append(
                f"duration {dur/60:.1f} min vs expected {expect_duration/60:.1f} min "
                f"({delta/60:.1f} min short) — parts are missing")
    return problems


def _duration_gap(problems: List[str], video_ts: str, expect_duration: float) -> List[str]:
    """Sum the VOB durations and compare with what the programme should run."""
    if problems:
        return []            # already flagged; a gap total would just add noise
    d = shlex.quote(video_ts)
    ffp = probe.probe_wrap(probe_ffprobe_entry())
    script = (
        f'total=0; for f in {d}/*.VOB {d}/*.vob; do [ -f "$f" ] || continue; '
        f'sz=$(stat -c %s "$f" 2>/dev/null); [ "$sz" -lt {MIN_MAIN_BYTES} ] && continue; '
        f'd=$({ffp} -v error -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null | head -1); '
        f'total=$(echo "$total + ${{d:-0}}" | bc 2>/dev/null || echo "$total"); done; echo "$total"'
    )
    try:
        total = float(_remote(script).strip().splitlines()[-1])
    except (ValueError, IndexError):
        return []
    if total <= 0:
        return []
    if abs(total - expect_duration) > 60:
        return [f"total VOB duration {total/60:.1f} min vs expected "
                f"{expect_duration/60:.1f} min — parts are missing"]
    return []
