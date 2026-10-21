"""Provider abstraction for metadata search.

Every provider answers two questions and nothing else:

    search(query, kind) -> [Candidate]
    fetch(candidate)    -> Record

Keeping the surface that small is what makes a new provider a single file
instead of another script. Normalisation lives in pure functions so it can be
tested without a network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: Field names the chain understands. Anything else a provider returns is kept
#: in `extra` so nothing is silently dropped.
STANDARD_FIELDS = (
    "name",
    "year",
    "biography",
    "portrait",
    "banner",
    "tmdb_id",
    "imdb_id",
    "mbid",
    "genres",
    "source_url",
)


@dataclass
class Candidate:
    """One search hit, before any detail fetch."""

    provider: str
    id: str
    name: str
    year: Optional[str] = None
    kind: str = "person"  # person | artist | movie | tv
    score: float = 0.0
    url: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "id": self.id,
            "name": self.name,
            "year": self.year,
            "kind": self.kind,
            "score": round(self.score, 3),
            "url": self.url,
        }


@dataclass
class Record:
    """Detail for one candidate.

    `provenance` maps field -> provider so a wrong value is traceable to the
    source that supplied it. That is the whole point of this module.
    """

    provider: str
    id: str
    fields: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, str] = field(default_factory=dict)

    def set(self, name: str, value: Any) -> None:
        if value in (None, "", [], {}):
            return
        self.fields[name] = value
        self.provenance[name] = self.provider

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "id": self.id,
            "fields": self.fields,
            "provenance": self.provenance,
        }


class Provider:
    """Adapter base. Subclasses set `name` and implement search/fetch."""

    name = "base"
    #: Minimum seconds between calls (per-provider politeness).
    min_interval = 0.5
    #: Whether a credential is required (reported by `available()`).
    requires_key = False

    def available(self) -> bool:
        return True

    def search(self, query: str, kind: str = "person") -> List[Candidate]:
        raise NotImplementedError

    def fetch(self, cand: Candidate) -> Optional[Record]:
        raise NotImplementedError


#: Only identity-bearing fields can be genuinely in conflict. Per-provider
#: metrics (listeners, album_count) and per-provider URLs differ by nature and
#: would otherwise flood the conflicts list with noise.
CONFLICT_FIELDS = frozenset(
    {"name", "year", "birth_year", "tmdb_id", "imdb_id", "mbid"}
)


def _name_key(value: Any) -> str:
    """Comparison form for names: drop parentheticals and punctuation.

    'The Chills (NZL)' and 'The Chills' are the same act; only the mattering
    difference (a genuinely different name) should be reported as a conflict.
    """
    s = re.sub(r"\([^)]*\)", " ", str(value or ""))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


#: Providers that may never supply a biography, even as a last resort. Their
#: same-name collisions are not detectable (a 2,000-char bio about a French
#: power-metal band was returned for the Sarah Records band 'Heavenly').
BIOGRAPHY_BLOCKED = frozenset({"theaudiodb"})


def merge(
    records: List[Record], prose_priority: List[str], portrait_priority: List[str]
) -> Dict[str, Any]:
    """Merge provider records, choosing each field by priority.

    Returns {'fields', 'provenance', 'conflicts'} where conflicts records every
    field where providers disagreed — a conflicting year is a strong signal the
    providers are talking about different people.
    """
    fields: Dict[str, Any] = {}
    prov: Dict[str, str] = {}
    seen: Dict[str, List[Dict[str, Any]]] = {}

    # Any provider not named in a priority list still contributes — it is simply
    # ranked last. Dropping unknown providers silently would make a newly added
    # adapter look like it found nothing.
    have = [r.provider for r in records]
    prose_order = list(prose_priority) + [p for p in have if p not in prose_priority]
    portrait_order = list(portrait_priority) + [
        p for p in have if p not in portrait_priority
    ]

    for name in ("biography",):
        for p in prose_order:
            if p in BIOGRAPHY_BLOCKED:
                continue
            rec = next((r for r in records if r.provider == p), None)
            if rec and rec.fields.get(name):
                fields[name] = rec.fields[name]
                prov[name] = rec.provider
                break

    for name in ("portrait", "banner"):
        for p in portrait_order:
            rec = next((r for r in records if r.provider == p), None)
            if rec and rec.fields.get(name):
                fields[name] = rec.fields[name]
                prov[name] = rec.provider
                break

    for rec in records:
        for name, value in rec.fields.items():
            if name in ("biography", "portrait", "banner"):
                continue
            if name == "source_url":
                # Attribute the URL to whoever supplied the biography; a
                # provider that contributed no prose must not own the citation.
                bio_prov = prov.get("biography")
                src_rec = next((r for r in records if r.provider == bio_prov), None)
                if src_rec and src_rec.fields.get("source_url"):
                    fields["source_url"] = src_rec.fields["source_url"]
                    prov["source_url"] = src_rec.provider
                elif "source_url" not in fields:
                    fields["source_url"] = value
                    prov["source_url"] = rec.provider
                continue
            if name not in fields:
                fields[name] = value
                prov[name] = rec.provider
            elif fields[name] != value and name in CONFLICT_FIELDS:
                same = name == "name" and _name_key(fields[name]) == _name_key(value)
                if not same:
                    seen.setdefault(
                        name, [{"provider": prov[name], "value": fields[name]}]
                    )
                    seen[name].append({"provider": rec.provider, "value": value})

    return {
        "fields": fields,
        "provenance": prov,
        "conflicts": {k: v for k, v in seen.items()},
    }
