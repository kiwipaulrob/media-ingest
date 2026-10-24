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
#: Beyond this the picture is missing, not merely out of step.
AVSYNC_TRUNCATION_TOLERANCE_S = 5.0


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
    _rc, raw = probe.query(cmd, timeout=300)
    raw = (raw or "").strip()
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
    # An unreported start_time is UNKNOWN, not 0.0 — coercing it made a track at +0.3s
    # look like it disagreed with a track whose time simply could not be read.
    starts = [s for s in (_as_float(a.get("start_time")) for a in audio) if s is not None]
    if len(starts) > 1 and (max(starts) - min(starts)) * 1000.0 >= START_FAIL_MS:
        problems.append(f"audio tracks disagree by {(max(starts)-min(starts))*1000:.0f} ms")

    # Container integrity: timestamps can agree while the index is damaged, and a player
    # then estimates times — drifting more the longer it runs. Goldwyn's fault exactly:
    # both streams measured within 0.8 s of each other, yet the picture drifted in playback.
    try:
        health = index_health(remote_path)
    except Exception:  # noqa: BLE001
        health = {"verdict": "unknown"}
    if health.get("verdict") == "unreliable":
        warnings.append(
            f"container index damaged ({health.get('untimed')}/{health.get('sampled')} "
            "packets untimed) — timestamps unreliable, rebuild recommended")

    verdict = "fail" if problems else ("warn" if warnings else "ok")
    return {
        "ok": not problems,
        "verdict": verdict,
        "index_health": health,
        "problems": problems,
        "warnings": warnings,
        "start_delta_ms": worst_start_ms,
        "video_duration": v_dur,
        "durations_derived": derived,
        "audio_tracks": len(audio),
    }


def index_health(remote_path: str, at: str = "50%", window: int = 2) -> Dict[str, Any]:
    """Are the stream's packet timestamps readable, or is the index damaged?

    A file can have matching durations and a full frame count yet still play out of sync,
    because the container's timestamps are missing and the player falls back to estimating
    them — an estimate that degrades the longer it runs. Sampling a couple of seconds is
    enough to tell: a healthy MKV reports a timestamp on every packet.

    Measured: damaged AVI at the midpoint 53/60 packets untimed, healthy MKV 0/49.
    """
    ff = probe.probe_wrap(probe.config.FFPROBE_ENTRY)
    cmd = (f"{ff} -v error -select_streams v:0 -show_entries packet=pts_time -of csv=p=0 "
           f"-read_intervals '{at}+{window}' {shlex.quote(remote_path)} 2>/dev/null")
    _rc, raw = probe.query(cmd, timeout=300)
    raw = raw or ""
    rows = [r.strip() for r in raw.splitlines() if r.strip()]
    if not rows:
        return {"verdict": "unknown", "sampled": 0, "untimed": 0, "ratio": None}
    untimed = sum(1 for r in rows if "N/A" in r)
    ratio = untimed / len(rows)
    if ratio >= 0.10:
        verdict = "unreliable"
    elif ratio > 0.0:
        verdict = "partial"
    else:
        verdict = "ok"
    return {"verdict": verdict, "sampled": len(rows), "untimed": untimed,
            "ratio": round(ratio, 3), "at": at}


def video_present_at_tail(remote_path: str, tail_seconds: int = 10) -> Optional[bool]:
    """Does the VIDEO stream decode anything in the last few seconds?

    ~1-3 s, versus a full packet scan. False means the picture stops early, which is
    terminal (no re-sync can invent missing frames), so resync() checks this first.
    None means the probe could not tell.
    """
    ff = probe.probe_wrap(probe.config.FFMPEG_ENTRY)
    cmd = (f"{ff} -v error -stats -sseof -{tail_seconds} -i {shlex.quote(remote_path)} "
           f"-map 0:v:0 -frames:v 1 -f null - 2>&1 | tr '\r' '\n' "
           f"| grep -o 'frame=[ ]*[0-9]*' | tail -1")
    try:
        raw = (probe.on_media(cmd, timeout=300) or "").strip()
    except Exception:  # noqa: BLE001
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    return (int(digits) > 0) if digits else None


def resync(remote_path: str, out_path: str) -> Dict[str, Any]:
    """Try to fix an A/V mismatch WITHOUT re-encoding the picture.

    Three faults look identical in a duration check but need different treatment:

    * **video truncated** — the picture simply stops early. Nothing to re-sync; the file
      needs replacing. Reported, never "repaired" (the Sabbath case needed a full rebuild
      from source; the John Lennon case needs a different copy).
    * **audio starts late/early** — a constant offset. Fixed losslessly by shifting the
      audio with ``-itsoffset`` and stream-copying both tracks.
    * **audio timeline longer than the picture** — broken MPEG timestamps (the +920 ms
      Sabbath drift). Fixed by regenerating ONLY the audio timeline (``asetpts``) and
      re-encoding audio to AAC while the video is stream-copied: cheap, and the picture is
      never degraded.

    Returns {'ok', 'reason', 'method', 'before', 'after'}.
    """
    # Cheap terminal check first: missing frames cannot be repaired, and this costs
    # seconds where the packet scans below cost a minute and a half.
    if video_present_at_tail(remote_path) is False:
        # deliberately no full check() here: it re-scans both streams for diagnostics
        # that nobody acts on, and turned a 3-second refusal into a minute.
        return {"ok": False, "reason": "video-truncated", "fast_path": True}

    before = check(remote_path)
    v_end = _derived_duration(remote_path, "v:0")
    a_end = _derived_duration(remote_path, "a:0")
    if v_end is None or a_end is None:
        return {"ok": False, "reason": "unmeasurable", "before": before}
    drift = round(a_end - v_end, 3)
    start_ms = float(before.get("start_delta_ms") or 0.0)

    # Picture ends far before the audio: the video is truncated, not desynced.
    if drift > AVSYNC_TRUNCATION_TOLERANCE_S:
        return {"ok": False, "reason": "video-truncated", "drift_s": drift,
                "video_end": v_end, "audio_end": a_end, "before": before}

    ff = probe.probe_wrap(probe.config.FFMPEG_ENTRY)
    quoted_in, quoted_out = shlex.quote(remote_path), shlex.quote(out_path)

    if abs(start_ms) >= 250.0:
        shift = -start_ms / 1000.0
        # Two inputs of the same file: input 0 is the picture, input 1 is the audio with
        # the offset applied. Mapping 0:a:0 would copy the UNSHIFTED audio and the repair
        # would report success while changing nothing (measured: still +400 ms).
        cmd = (f"{ff} -y -v error -i {quoted_in} -itsoffset {shift:.3f} -i {quoted_in} "
               f"-map 0:v:0 -map 1:a:0 -c copy {quoted_out}")
        method = "itsoffset-copy"
    elif drift > 0.75:
        # Audio runs long. A plain trim does NOT fix broken timestamps (stream copy keeps
        # them), so this is tried first and audio-retime is the fallback below.
        cmd = f"{ff} -y -v error -i {quoted_in} -map 0 -c copy -shortest {quoted_out}"
        method = "trim-copy"
    elif drift < -0.75:
        # Audio SHORT of the picture. Do NOT use -shortest here: that would cut the
        # picture down to the missing audio. Pad the audio instead so the video survives.
        cmd = (f"{ff} -y -v error -i {quoted_in} -map 0:v:0 -map 0:a:0 -c:v copy "
               f"-af \"asetpts=N/SR/TB,apad\" -c:a aac -b:a 192k -shortest {quoted_out}")
        method = "audio-pad-retime"
    else:
        return {"ok": True, "reason": "already-in-sync", "method": "none", "before": before}

    try:
        probe.on_media(cmd, timeout=3600)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"reencode-failed: {str(exc)[:120]}",
                "method": method, "before": before}

    after = check(out_path)

    # A stream-copy trim cannot repair a broken audio timeline. If it did not verify,
    # retry once by regenerating the audio timestamps (video still stream-copied).
    if after.get("verdict") == "fail" and method == "trim-copy":
        retry = (f"{ff} -y -v error -i {quoted_in} -map 0:v:0 -map 0:a:0 -c:v copy "
                 f"-af \"asetpts=N/SR/TB,apad\" -c:a aac -b:a 192k -shortest {quoted_out}")
        try:
            probe.on_media(retry, timeout=3600)
            method = "trim-then-retime"
            after = check(out_path)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": after.get("verdict") != "fail", "reason": "verified" if after.get("verdict")
            != "fail" else "still-out-of-sync", "method": method,
            "before": before, "after": after}


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
