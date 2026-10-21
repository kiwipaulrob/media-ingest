"""Two-phase in-place replacement.

Phase A copies and REPORTS FACTS. Python applies the gates. Only then does
Phase B retire the old file. The gates live in Python (not in the shell)
because the safety logic is the code that most needs to be testable.

Gate order, each with a real incident behind it:
  STAGE OK        <- a failed copy must never reach a delete
  episode match   <- a season pack's biggest file was the wrong episode
  title match     <- the copied file's own tag must contradict the target
  duration match  <- a genuinely different work rarely has the same runtime
  decode ok       <- a completed copy is not proof of playability

Execution runs inside CT105 on prox2 (the only namespace that can see both the
library and the downloads) and uses the jellyfin image for ffprobe/ffmpeg,
because neither the host nor the CT has those binaries installed.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Dict, Optional, Tuple

from . import config, matcher, probe

PHASE_A = r"""
set -u
SRC={src}; DST={dst}; OLD={old}; IMG={img}; VOL={vol}
FFP="docker run --rm -v $VOL:$VOL --entrypoint {ffprobe_entry} $IMG"
FFF="docker run --rm -v $VOL:$VOL --entrypoint {ffmpeg_entry} $IMG"
mkdir -p "$(dirname "$DST")" || {{ echo "STAGE=MKDIR-FAIL"; exit 3; }}
[ -s "$SRC" ] || {{ echo "STAGE=SRC-MISSING"; exit 3; }}
SAME=0
[ "$DST" = "$OLD" ] && SAME=1
TARGET="$DST"; [ "$SAME" = "1" ] && TARGET="$DST.new"
SIZE=$(stat -c%s "$SRC")
if [ -f "$TARGET" ] && [ "$(stat -c%s "$TARGET")" = "$SIZE" ]; then
  echo "COPIED=already"
else
  cp --reflink=auto --sparse=always "$SRC" "$TARGET.part" || {{ echo "STAGE=COPY-FAIL"; exit 3; }}
  mv -f "$TARGET.part" "$TARGET" || {{ echo "STAGE=MOVE-FAIL"; exit 3; }}
  echo "COPIED=fresh"
fi
sync "$TARGET" 2>/dev/null || true
PROBE=$($FFP -v error -select_streams v:0 -show_entries stream=width,height,codec_name -of csv=p=0 "$TARGET" 2>/dev/null)
[ -n "$PROBE" ] || {{ echo "STAGE=PROBE-FAIL"; exit 4; }}
echo "PROBE=$PROBE"
echo "TITLE=$($FFP -v error -show_entries format_tags=title -of default=nw=1:nk=1 "$TARGET" 2>/dev/null | head -1)"
echo "NDUR=$($FFP -v error -show_entries format=duration -of csv=p=0 "$TARGET" 2>/dev/null)"
echo "ODUR=$($FFP -v error -show_entries format=duration -of csv=p=0 "$OLD" 2>/dev/null)"
MID=$(awk -v d="$($FFP -v error -show_entries format=duration -of csv=p=0 "$TARGET" 2>/dev/null)" 'BEGIN{{ if (d+0 > 4) printf "%d", d/2; else print 1 }}')
if $FFF -v error -ss "$MID" -i "$TARGET" -t 8 -f null - >/dev/null 2>&1; then
  echo "DECODE=ok"
else
  echo "DECODE=fail"
  echo "STAGE=DECODE-FAIL"; exit 5
fi
echo "SAME=$SAME"
echo "STAGE=OK"; exit 0
"""

PHASE_B = r"""
set -u
DST={dst}; OLD={old}
if [ "{same}" = "1" ]; then
  mv -f "$DST.new" "$DST" || {{ echo "COMMIT=MV-FAIL"; exit 6; }}
  echo "COMMIT=overwritten"
else
  [ -f "$OLD" ] && {{ rm -f "$OLD" || {{ echo "COMMIT=RM-FAIL"; exit 6; }}; }}
  echo "COMMIT=old-removed"
fi
echo "COMMIT=OK"; exit 0
"""

PHASE_ROLLBACK = r"""
set -u
DST={dst}
[ "{same}" = "1" ] && rm -f "$DST.new" || rm -f "$DST"
echo "ROLLBACK=done"
"""


class SwapError(RuntimeError):
    """A gate refused the swap; the old file is untouched."""


def parse_kv(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def part_num(s: str) -> Optional[int]:
    """Ordinal of an episode/part in a name ('S01E05'->5, 'part 1'->1)."""
    m = re.search(r"(?i)\bs\d{1,2}\s*e(\d{1,3})\b", s or "")
    if m:
        return int(m.group(1))
    m = re.search(
        r"(?i)\b(?:part|pt|episode|ep|cd|disc|disk)\s*\.?\s*(\d{1,3})\b", s or ""
    )
    if m:
        return int(m.group(1))
    return None


def _phase_a(src: str, dst: str, old: str) -> Tuple[int, str]:
    inner = PHASE_A.format(
        src=shlex.quote(src),
        dst=shlex.quote(dst),
        old=shlex.quote(old),
        img=shlex.quote(config.media_image()),
        vol=shlex.quote(config.MEDIA_VOLUME),
        ffprobe_entry=config.FFPROBE_ENTRY,
        ffmpeg_entry=config.FFMPEG_ENTRY,
    )
    return _run(inner)


def _run(inner: str) -> Tuple[int, str]:
    try:
        return 0, probe.on_media(inner, timeout=7200)
    except RuntimeError as exc:
        return 1, str(exc)
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"


def replace(src: str, dst: str, old: str, *, old_name: str = "") -> Tuple[str, str]:
    """Swap `src` in at `dst`, retiring `old`. Returns (status, detail).

    Raises SwapError on any gate failure; the old file is then intact.
    """
    same = dst == old
    rc, out = _phase_a(src, dst, old)
    kv = parse_kv(out)
    if kv.get("STAGE") != "OK":
        raise SwapError(
            f'stage-failed: {kv.get("STAGE") or f"rc={rc} {out.strip()[:160]}"}'
        )

    # gate 1: the copied file's own episode number must agree with the target
    title = kv.get("TITLE") or ""
    t_part, i_part = part_num(title), part_num(old_name) or part_num(
        os.path.basename(old)
    )
    if t_part is not None and i_part is not None and t_part != i_part:
        _run(PHASE_ROLLBACK.format(dst=shlex.quote(dst), same="1" if same else "0"))
        raise SwapError(f"episode-mismatch: file={t_part} target={i_part}")

    # gate 2: an embedded tag must not contradict the target
    if title:
        t_tokens = matcher.distinctive(matcher.tokens(matcher.clean_release(title)))
        i_tokens = matcher.distinctive(matcher.tokens(matcher.clean_release(old_name)))
        if t_tokens and i_tokens and not (t_tokens & i_tokens):
            _run(PHASE_ROLLBACK.format(dst=shlex.quote(dst), same="1" if same else "0"))
            raise SwapError(f"content-mismatch: tag={title[:60]!r}")

    # gate 3: runtime must agree (only meaningful when the target is a file)
    if not same:
        try:
            nd = float(kv.get("NDUR") or 0)
            od = float(kv.get("ODUR") or 0)
        except ValueError:
            nd = od = 0.0
        if nd > 60 and od > 60:
            diff = abs(nd - od) / max(nd, od)
            if diff > config.DURATION_TOLERANCE:
                _run(PHASE_ROLLBACK.format(dst=shlex.quote(dst), same="0"))
                raise SwapError(
                    f"duration-mismatch: {nd/60:.1f} vs {od/60:.1f} min ({diff*100:.0f}%)"
                )

    rc2, out2 = _run(
        PHASE_B.format(
            dst=shlex.quote(dst), old=shlex.quote(old), same="1" if same else "0"
        )
    )
    kv2 = parse_kv(out2)
    if kv2.get("COMMIT") != "OK":
        raise SwapError(f'commit-failed: {kv2.get("COMMIT") or f"rc={rc2}"}')
    return "replaced", f"{kv2.get('COMMIT')}, probe {kv.get('PROBE')}"
