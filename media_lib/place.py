"""Stage 5 — place a completed download as a NEW library item.

Why this stage exists: `apply` retires an old file, so it can only ever REPLACE
a row that already has one. A genuinely new episode has nothing to retire.

Measured cost model (prox2 / CT105, a 1.3 GB episode):
  * one `docker run ... ffprobe` round-trip    1.42 s
  * one `probe.head_bytes` round-trip          1.08 s
  * copy of 1.3 GB onto the CIFS-backed share  ~33 s  (≈39 MB/s effective —
    well under the 158 MB/s in config.COPY_MBPS, which is a local-disk figure
    and overstates this path ~4x; budget placements at ~40 MB/s)

The first version made SIX remote calls per file (probe src, stage, probe dst,
three decode spans). Each pays SSH plus container startup, so the gates cost
more than they protect. This version folds them into TWO: one shell call stages
and probes BOTH ends, one decodes every span. That is ~5.7 s saved per item —
about 13% of a 42 s placement — with no gate lost, and it adds two gates the
first version lacked.

Gates (each removes the staged copy on failure):
  SIZE      src bytes == dst bytes      <- a truncated copy still probes clean
  PROBE     dst streams readable
  HEIGHT    dst >= min_height
  DURATION  dst within tolerance of SRC <- measured against the source, so it
                                            always runs instead of depending on a
                                            caller passing –expect
  DECODE    start/middle/end all decode clean

Nothing here guesses a destination: the scanner keys rows off the PATH, so a
normalised-title guess creates a second row instead of filling the intended one.
Placement does not make Jellyfin notice the file — call `jellyfin.notify_media`
afterwards (a library Refresh re-resolves KNOWN rows and will not discover new
ones; that omission is why two placements sat invisible until forced).
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any, Dict, Optional

from . import avsync, completeness, config, probe

DECODE_SPANS = (0.05, 0.50, 0.95)
M_SRC = "@@SRC@@"
M_DST = "@@DST@@"
M_END = "@@END@@"


def to_disk(path: str) -> str:
    """Map a Jellyfin/container path onto the media host's disk path."""
    mapping = {**config.JF_TO_DISK, **config.SAVE_TO_REMOTE}
    for jf_prefix, disk_prefix in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        if path.startswith(jf_prefix):
            return disk_prefix + path[len(jf_prefix):]
    return path


def _num(pattern: str, out: str) -> int:
    """First capture group as int, 0 when absent."""
    m = re.search(pattern, out)
    return int(m.group(1)) if m else 0


def _block(out: str, marker: str) -> Optional[Dict[str, Any]]:
    """Pull one JSON block out of a multi-section shell report."""
    start = out.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = out.find(M_END, start)
    if end < 0:
        return None
    try:
        return json.loads(out[start:end].strip() or "{}")
    except json.JSONDecodeError:
        return None


def _stats(blob: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not blob:
        return {}
    s = (blob.get("streams") or [{}])[0] if blob.get("streams") else {}
    fmt = blob.get("format") or {}
    try:
        dur = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return {
        "w": s.get("width"),
        "h": s.get("height"),
        "codec": s.get("codec_name"),
        "duration": dur,
        "title": (fmt.get("tags") or {}).get("title") or "",
    }


def _stage_script(src_d: str, dst_d: str) -> str:
    """Stage the copy and probe BOTH ends in a single remote call."""
    ffp = probe.probe_wrap(config.FFPROBE_ENTRY)
    probe_expr = (
        "-v error -select_streams v:0 "
        "-show_entries stream=width,height,codec_name:format=duration:format_tags=title "
        "-of json"
    )
    return f"""
set -u
SRC={shlex.quote(src_d)}; DST={shlex.quote(dst_d)}
[ -s "$SRC" ] || {{ echo STAGE=SRC-MISSING; exit 3; }}
mkdir -p "$(dirname "$DST")" || {{ echo STAGE=MKDIR-FAIL; exit 3; }}
SIZE_SRC=$(stat -c%s "$SRC")
if [ -f "$DST" ] && [ "$(stat -c%s "$DST")" = "$SIZE_SRC" ]; then
  COPIED=already
else
  rm -f "$DST.part"
  if ! cp --reflink=auto --sparse=always "$SRC" "$DST.part"; then
    rm -f "$DST.part"; echo STAGE=COPY-FAIL; exit 3
  fi
  mv -f "$DST.part" "$DST" || {{ rm -f "$DST.part"; echo STAGE=MOVE-FAIL; exit 3; }}
  COPIED=fresh
fi
sync "$DST" 2>/dev/null || true
echo COPIED=$COPIED
echo SIZE_SRC=$SIZE_SRC
echo SIZE_DST=$(stat -c%s "$DST")
echo {M_SRC}
{ffp} {probe_expr} "$SRC" 2>/dev/null || true
echo {M_END}
echo {M_DST}
{ffp} {probe_expr} "$DST" 2>/dev/null || true
echo {M_END}
"""


def _decode_script(dst_d: str, duration: float) -> str:
    """Every decode span in ONE remote call."""
    fff = probe.probe_wrap(config.FFMPEG_ENTRY)
    lines = ["set -u", f"DST={shlex.quote(dst_d)}"]
    for frac in DECODE_SPANS:
        ss = max(0.0, duration * frac)
        lines.append(
            f'if {fff} -v error -ss {ss:.1f} -t 5 -i "$DST" -f null - -xerror >/dev/null 2>&1; '
            f"then echo DECODE={frac:.2f}=OK; else echo DECODE={frac:.2f}=FAIL; fi"
        )
    return "\n".join(lines)


#: Audio running past the picture by more than this is drift, not a benign tail gap.
AV_SYNC_FAIL_MS = 750.0
#: A start offset this large is audible from the first line of dialogue.
AV_SYNC_START_FAIL_MS = 250.0


def place_new(
    src: str,
    dest: str,
    *,
    expect_duration: Optional[float] = None,
    min_height: int = 0,
    max_duration_delta: float = 3.0,
) -> Dict[str, Any]:
    """Copy `src` to `dest` and pass every gate before calling it placed."""
    src_d, dst_d = to_disk(src), to_disk(dest)
    if not dst_d.lower().endswith((".mkv", ".mp4", ".avi")):
        return {"ok": False, "stage": "BAD-DEST", "dest": dest}

    # Completeness FIRST: every gate below inspects the copy, so a source that is
    # missing parts would sail through all of them. A DVD chain with a hole, or one
    # whose timestamps stall, is refused here rather than placed and discovered later.
    problems = completeness.source_problems(src_d, expect_duration=expect_duration)
    if problems:
        return {
            "ok": False,
            "stage": "INCOMPLETE-SOURCE",
            "detail": "; ".join(problems[:3]),
            "problems": problems,
            "dest": dest,
        }

    try:
        stage = probe.on_media(_stage_script(src_d, dst_d), timeout=3600)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "stage": "STAGE-EXEC-FAIL",
            "detail": f"{type(exc).__name__}: {str(exc)[:160]}",
            "dest": dest,
        }
    if "STAGE=" in stage:
        return {"ok": False, "stage": stage.split("STAGE=")[1].split()[0], "dest": dest}

    def _rm() -> None:
        """A failed gate must never leave a half-good file in the library."""
        try:
            probe.on_media(
                f"rm -f {shlex.quote(dst_d)} {shlex.quote(dst_d)}.part", timeout=60
            )
        except Exception:  # noqa: BLE001
            pass

    size_src = _num(r"SIZE_SRC=(\d+)", stage)
    size_dst = _num(r"SIZE_DST=(\d+)", stage)
    if size_src and size_dst != size_src:
        _rm()
        return {
            "ok": False,
            "stage": "SIZE-MISMATCH",
            "detail": f"{size_dst} != {size_src}",
            "dest": dest,
        }

    src_s, dst_s = _stats(_block(stage, M_SRC)), _stats(_block(stage, M_DST))
    if not dst_s.get("h"):
        _rm()
        return {"ok": False, "stage": "DST-PROBE-FAIL", "dest": dest}
    if dst_s["h"] < min_height:
        _rm()
        return {
            "ok": False,
            "stage": f"BELOW-MIN-HEIGHT({dst_s['h']}<{min_height})",
            "dest": dest,
        }

    ref = expect_duration or src_s.get("duration") or 0
    if ref and abs(dst_s["duration"] - ref) > max_duration_delta:
        _rm()
        return {
            "ok": False,
            "stage": "DURATION-MISMATCH",
            "detail": f"{dst_s['duration']:.1f}s vs {ref:.1f}s",
            "dest": dest,
        }

    try:
        dec = probe.on_media(_decode_script(dst_d, dst_s["duration"]), timeout=600)
    except Exception as exc:  # noqa: BLE001
        dec = f"DECODE=err=FAIL {type(exc).__name__}"
    if "FAIL" in dec:
        _rm()
        return {
            "ok": False,
            "stage": "DECODE-FAIL",
            "detail": dec.strip().replace("\n", " ")[:120],
            "dest": dest,
        }

    # A/V SYNC GATE. A stream copy or a DVD chain can leave the audio timeline longer
    # than the picture (broken MPEG timestamps), which is audible and survives every gate
    # above: duration matches, the file decodes, the height is right. Sign matters —
    # audio running PAST the picture is drift; audio ending a second or two early is just
    # trailing credits with no sound, so that is a warning, not a refusal.
    try:
        sync = avsync.check(dst_d)
    except Exception as exc:  # noqa: BLE001
        sync = {"verdict": "probe-failed", "problems": [f"{type(exc).__name__}"]}
    sync_stage = "ok"
    if sync.get("verdict") == "fail":
        drift = 0.0
        for prob in sync.get("problems") or []:
            m = re.search(r"differs by ([+-]?\d+)", str(prob))
            if m:
                drift = float(m.group(1))
        if drift > AV_SYNC_FAIL_MS or sync.get("start_delta_ms") and abs(
                float(sync.get("start_delta_ms") or 0)) >= AV_SYNC_START_FAIL_MS:
            # Try to re-sync before giving up: an offset or a long audio track is fixable
            # by stream-copying the picture, so the video is never re-encoded. Truncation
            # is not fixable here and resync() says so.
            repaired = dst_d + ".resync.mkv"
            try:
                fix = avsync.resync(dst_d, repaired)
            except Exception as exc:  # noqa: BLE001
                fix = {"ok": False, "reason": f"{type(exc).__name__}"}
            if fix.get("ok"):
                try:
                    probe.on_media(
                        f"mv -f {shlex.quote(repaired)} {shlex.quote(dst_d)}", timeout=300)
                    sync_stage = "repaired"
                except Exception:  # noqa: BLE001
                    fix = {"ok": False, "reason": "replace-failed"}
            if not fix.get("ok"):
                probe.on_media(f"rm -f {shlex.quote(repaired)}", timeout=120)
                _rm()
                return {
                    "ok": False,
                    "stage": "AV-SYNC-FAIL",
                    "detail": f"{'; '.join(sync.get('problems') or [])[:130]} "
                              f"[repair: {fix.get('reason')}]"[:200],
                    "sync": sync,
                    "repair": fix,
                    "dest": dest,
                }
        else:
            sync_stage = "warn"
    elif sync.get("verdict") == "probe-failed":
        sync_stage = "unknown"

    copied_match = re.search(r"COPIED=(\w+)", stage)
    copied = copied_match.group(1) if copied_match else "unknown"
    return {
        "ok": True,
        "stage": "PLACED",
        "dest": dest,
        "res": f"{dst_s['w']}x{dst_s['h']} {dst_s['codec']}",
        "duration": round(dst_s["duration"], 1),
        "size_gb": round(size_dst / 1e9, 2),
        "copied": copied,
        "av_sync": sync_stage,
        "index_health": (sync.get("index_health") or {}).get("verdict"),
        "needs_rebuild": (sync.get("index_health") or {}).get("verdict") == "unreliable",
    }
