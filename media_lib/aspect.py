"""Aspect-flag verdicts: does a file's container DAR contradict its raster?

Proven class: 720x400 XviD with SAR 25:36 (DAR 5:4) on widescreen content
plays squeezed; the pixels are right, the flag is wrong. Legit anamorphic
discs (720x576 -> 16:9) must never flag.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


def parse_ratio(ar: str) -> Optional[float]:
    """'16:9' -> 1.778, '1.85:1' -> 1.85. None when unparseable. Pure."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$", ar or "")
    if not m or float(m.group(2)) == 0:
        return None
    return float(m.group(1)) / float(m.group(2))


def classify(ar: str, w: int, h: int) -> Tuple[str, str]:
    """(verdict, reason) for one file. Pure.

    Verdicts: 'ok' | 'suspect-narrow' | 'suspect-wide' | 'unknown'.
    """
    dar = parse_ratio(ar)
    if not dar or not w or not h:
        return "unknown", f"ar={ar} raster={w}x{h}"
    raster = w / h
    if raster >= 1.7 and dar < 1.5:
        return ("suspect-narrow",
                f"wide raster {w}x{h} ({raster:.2f}) flagged DAR {ar} — "
                f"plays squeezed; SAR repair candidate")
    if raster < 1.2 and dar >= 1.7:
        # Below anamorphic territory (720x576 = 1.25 is legit): a near-square
        # raster flagged widescreen is almost never correct.
        return ("suspect-wide",
                f"near-square raster {w}x{h} ({raster:.2f}) flagged DAR {ar}")
    return "ok", f"raster {raster:.2f} vs DAR {dar:.2f}"
