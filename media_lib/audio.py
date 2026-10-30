"""Audio-language verdicts: Android-friendly soundtrack + bloat check.

Ingest-time advisory (warn, never refuse — like ASPECT-FLAG). Doctrine:

- Android-friendly = an AAC stereo track in a language the household keeps
  (English or Italian). Moana (2026) arrived with TrueHD + 13 AC3 tracks and
  no AAC at all: unplayable on Android until remuxed.
- Bloat = audio tracks in languages the household does not keep. Moana
  carried 10 non-English/Italian dubs (~10 GB). Strip at ingest EXCEPT:
  (1) keep foreign-language tracks when NO English track exists (that is the
  only soundtrack, not bloat); (2) always keep Italian.
"""

from __future__ import annotations

from typing import Dict, List

KEEP = ("eng", "ita")


def verdict(audio: List[Dict]) -> List[str]:
    """Advisory notes for an audio track list. Pure. Empty = nothing to say."""
    tracks = [a for a in (audio or []) if isinstance(a, dict)]
    if not tracks:
        return ["AUDIO-UNKNOWN: no audio streams probed"]
    langs = [(str(a.get("lang") or "").lower() or "und") for a in tracks]

    def is_aac_stereo(a: Dict) -> bool:
        return (str(a.get("codec") or "").lower() == "aac" and
                (a.get("channels") or 0) <= 2)

    notes: List[str] = []
    has_eng = any(L == "eng" for L in langs)
    kept = [a for a, L in zip(tracks, langs) if L in KEEP or (not has_eng)]
    if has_eng and not any(is_aac_stereo(a) and
                           str(a.get("lang") or "").lower() in KEEP
                           for a in tracks):
        notes.append("AUDIO-NO-AAC: no AAC stereo in English/Italian — "
                     "Android will transcode or refuse; remux candidate")
    drop = len(tracks) - len(kept)
    if drop > 0:
        notes.append(f"AUDIO-BLOAT: {drop} non-English/Italian dub(s) "
                     "strippable at ingest "
                     f"({', '.join(sorted({L for L in langs if L not in KEEP}))})")
    return notes
