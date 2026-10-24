"""Audio/video sync assertions for ingested media.

Honest scope. This detects CONTAINER-level faults:

* audio starting at a different offset than video (the classic WEBRip/DVD fault,
  audible from the first line of dialogue),
* stream durations that disagree beyond a tolerance (drift — gets worse the
  longer you watch),
* a missing audio track, or several audio tracks with different offsets,
* an AAC/AC-3 priming delay that no edit list compensates (a constant few-ms
  lead, normally inaudible, but it compounds with a bad mux).

It CANNOT judge subjective lip-sync. An attempt to correlate on-screen motion
against the audio envelope across four windows of real content peaked at r≈0.06
with lags scattered over 1.7 s — that is noise, and acting on it would have
invented an offset and made a good file worse. So a pass here means "nothing is
mechanically wrong", NOT "the dialogue looks right". When a viewer reports a
sync problem on a file that passes, the offset has to come from them (early vs
late), not from a guess.
"""
from __future__ import annotations

import json
import shlex
from typing import Any, Dict, List, Optional

from . import probe  # noqa: E402

# A lip-sync error becomes noticeable around 100-150 ms; ITU-R BT.1359 puts the
# detectability threshold at roughly +45/-125 ms, so hold ingest to the tight end
# but only *warn* until it is clearly audible.
START_WARN_MS = 100
START_FAIL_MS = 250
# Streams legitimately differ by a frame or two at the tail; anything beyond this
# is a drift that will be visible by the closing credits.
DRIFT_WARN_MS = 500


def _as_float(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "N/A"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def streams(remote_path: str) -> List[Dict[str, Any]]:
    """ffprobe every stream with the timing fields sync depends on."""
    cmd = (
        f"{probe.probe_wrap(probe.config.FFPROBE_ENTRY)} -v error "
        f"-show_entries stream=index,codec_type,codec_name,start_time,duration,channels "
        f"-of json {shlex.quote(remote_path)}"
    )
    try:
        data = json.loads(probe.on_media(cmd, timeout=180) or "{}")
    except Exception:  # noqa: BLE001 - a probe failure must read as unknown, not as pass
        return []
    return data.get("streams") or []


def _derived_duration(remote_path: str, selector: str) -> Optional[float]:
    """Stream duration from its LAST packet timestamp when the container says N/A.

    MKV usually carries no per-stream duration, which silently disabled drift detection:
    ``v_dur and a_dur`` was always false, so a 900 ms audio/video mismatch read as "ok".
    Reading the final packet works for video and audio alike (audio has no frame rate, so
    counting packets and dividing cannot be used there).
    """
    cmd = (
        f"{probe.probe_wrap(probe.config.FFPROBE_ENTRY)} -v error -select_streams {selector} "
        f"-show_entries packet=pts_time,duration_time -of csv=p=0 {shlex.quote(remote_path)} "
        f"| tail -1"
    )
    try:
        raw = (probe.on_media(cmd, timeout=300) or "").strip()
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    try:
        pts = float(parts[0])
    except (TypeError, ValueError, IndexError):
        return None
    dur = 0.0
    if len(parts) > 1:
        try:
            dur = float(parts[1])
        except (TypeError, ValueError):
            dur = 0.0
    return round(pts + dur, 3)


def check(remote_path: str) -> Dict[str, Any]:
    """Compare the audio and video timelines. Never raises; returns a verdict."""
    rows = streams(remote_path)
    if not rows:
        return {"ok": False, "verdict": "probe-failed", "problems": ["ffprobe returned nothing"],
                "start_delta_ms": None, "drift_ms": None}

    video = [s for s in rows if s.get("codec_type") == "video"]
    audio = [s for s in rows if s.get("codec_type") == "audio"]
    if not video:
        return {"ok": False, "verdict": "no-video", "problems": ["no video stream"],
                "start_delta_ms": None, "drift_ms": None}
    if not audio:
        return {"ok": False, "verdict": "no-audio", "problems": ["no audio stream"],
                "start_delta_ms": None, "drift_ms": None}

    v_start = _as_float(video[0].get("start_time"))
    v_dur = _as_float(video[0].get("duration"))
    derived = False
    if v_dur is None:
        v_dur = _derived_duration(remote_path, "v:0")
        derived = v_dur is not None

    problems: List[str] = []
    warnings: List[str] = []
    worst_start_ms = 0.0
    for a_pos, a in enumerate(audio):
        a_start = _as_float(a.get("start_time"))
        a_dur = _as_float(a.get("duration"))
        if a_dur is None:
            # ffprobe selectors use the AUDIO ORDINAL, not the global stream index:
            # a one-audio file is a:0, so a:1 selected nothing and the drift check
            # silently passed. That is the exact failure this check exists to catch.
            a_dur = _derived_duration(remote_path, f"a:{a_pos}")
            derived = derived or a_dur is not None
        delta_ms = round(((a_start or 0.0) - (v_start or 0.0)) * 1000.0, 1)
        if abs(delta_ms) > abs(worst_start_ms):
            worst_start_ms = delta_ms
        label = f"audio #{a.get('index')} ({a.get('codec_name')})"
        if v_start is None or a_start is None:
            # A missing start_time is UNKNOWN, not zero. Coercing it to 0.0 made an
            # unprobeable stream read as perfectly aligned.
            warnings.append(f"{label} start_time not reported — offset unknown")
            continue
        if abs(delta_ms) >= START_FAIL_MS:
            problems.append(f"{label} starts {delta_ms:+.0f} ms from video")
        elif abs(delta_ms) >= START_WARN_MS:
            warnings.append(f"{label} offsets {delta_ms:+.0f} ms from video")
        if v_dur and a_dur:
            drift_ms = round((a_dur - v_dur) * 1000.0, 1)
            if abs(drift_ms) >= DRIFT_WARN_MS:
                problems.append(f"{label} duration differs by {drift_ms:+.0f} ms")

    # Several audio tracks that disagree with each other are a mux fault even when
    # each one is individually close to video.
    starts = [(_as_float(a.get("start_time")) or 0.0) for a in audio]
    if len(starts) > 1 and (max(starts) - min(starts)) * 1000.0 >= START_FAIL_MS:
        problems.append(f"audio tracks disagree by {(max(starts)-min(starts))*1000:.0f} ms")

    verdict = "fail" if problems else ("warn" if warnings else "ok")
    return {
        "ok": not problems,
        "verdict": verdict,
        "problems": problems,
        "warnings": warnings,
        "start_delta_ms": worst_start_ms,
        "video_duration": v_dur,
        "durations_derived": derived,
        "audio_tracks": len(audio),
    }


def describe(result: Dict[str, Any]) -> str:
    """One-line rendering for CLI output."""
    verdict = result.get("verdict")
    if verdict == "ok":
        return "sync ok (audio and video timelines agree)"
    if verdict == "warn":
        return "sync warning: " + "; ".join(result.get("warnings") or [])
    if verdict == "fail":
        return "SYNC FAIL: " + "; ".join(result.get("problems") or [])
    return f"sync {verdict}: " + "; ".join(result.get("problems") or [])
