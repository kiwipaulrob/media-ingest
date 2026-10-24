"""The provider chain: search across providers, resolve with provenance.

Priority order is a product decision, not an accident:

  prose      tmdb > lastfm > genius > wikipedia
  portraits  tmdb > deezer > lastfm > wikipedia (wikimedia thumbnail)

TMDB leads prose because its ids are the ones the library already stores, so a
TMDB-sourced field can be re-verified. Deezer leads portraits after TMDB because
its XL images are real photos rather than Last.fm's shared placeholder.

A failing provider is REPORTED per provider, never silently treated as
'no results' — otherwise a 429 reads as 'this person has no biography'.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .base import Candidate, Provider, Record, merge
from . import cache
from .deezer import DeezerProvider
from .genius import GeniusProvider
from .lastfm import LastfmProvider
from .musicbrainz import MusicBrainzProvider
from .theaudiodb import TheAudioDbProvider
from .tmdb_p import TmdbProvider
from .wikipedia import WikipediaProvider

#: TheAudioDB is NOT trusted for prose. Proven live: querying 'Heavenly' (the
#: Sarah Records indie-pop band) returns exactly ONE exact-name artist — a French
#: power-metal band — so an ambiguity guard cannot catch it and a 2,000-char bio
#: about the wrong band is the result. It is used for banners/portraits only
#: (it is the only provider here that supplies banners), unless a caller opts in.
PROSE_PRIORITY = ["tmdb", "lastfm", "genius", "wikipedia"]
PORTRAIT_PRIORITY = ["tmdb", "deezer", "lastfm", "theaudiodb", "wikipedia"]


def default_providers() -> List[Provider]:
    return [
        TmdbProvider(),
        LastfmProvider(),
        GeniusProvider(),
        DeezerProvider(),
        WikipediaProvider(),
        TheAudioDbProvider(),
        MusicBrainzProvider(),
    ]


def search(
    name: str, kind: str = "person", providers: Optional[List[Provider]] = None
) -> Dict[str, Any]:
    """Candidates from every provider, with per-provider status."""
    providers = providers if providers is not None else default_providers()
    candidates: List[Candidate] = []
    errors: Dict[str, str] = {}
    skipped: List[str] = []
    for p in providers:
        if not p.available():
            skipped.append(p.name)
            continue
        try:
            candidates.extend(p.search(name, kind))
        except cache.ProviderError as exc:
            errors[p.name] = str(exc)[:160]
        except Exception as exc:  # noqa: BLE001
            errors[p.name] = f"{type(exc).__name__}: {str(exc)[:120]}"
    candidates.sort(key=lambda c: -c.score)
    return {
        "query": name,
        "kind": kind,
        "candidates": [c.to_dict() for c in candidates],
        "_candidates": candidates,
        "errors": errors,
        "skipped": skipped,
    }


def _birth_year(rec) -> Optional[int]:
    try:
        y = rec.fields.get("birth_year")
        return int(str(y)[:4]) if y and str(y)[:4].isdigit() else None
    except (TypeError, ValueError):
        return None


def _split_plausible(records):
    """Separate modern subjects from pre-1900 collisions.

    A film/music library is modern by construction: a 'born 1853' biography for
    a name credited in a 2010s documentary is almost always a same-name
    historical figure. Younger candidates win; pre-1900 records are only used
    when nothing else exists, and then flagged for human review.
    """
    plausible: List[Dict[str, Any]] = []
    suspect: List[Dict[str, Any]] = []
    for r in records:
        y = _birth_year(r)
        (suspect if (y is not None and y < 1900) else plausible).append(r)
    return plausible, suspect


#: Text that is not a biography at all. Two families, both proven live:
#:
#: *SENSE LISTS* — Last.fm's placeholder for a shared name. The wording varies, and
#: a narrow list misses the commonest forms: "There is more than one artist with
#: the name Chris White: 1) an English jazz/rock saxophonist…" scored 530 chars and
#: was offered as `ready`, because the old pattern only knew "there are at least".
#: A numbered list after the opening is the same failure wearing a different hat.
#:
#: *DISAMBIGUATION PAGES* — Wikipedia's title gate cannot reject these (their title
#: IS the exact name), so they must be rejected on content. "Bernard MacMahon or
#: McMahon may refer to: …bishop… priest… horticulturist" is a page about a NAMESPACE.
_BIO_SENSE_LIST = re.compile(
    r"(there are at least|there is more than one|more than one artist|"
    r"there are \w+ artists|multiple artists|several artists|"
    r"is a disambiguation|this is a disambiguation|"
    r"may refer to|can refer to|might refer to|"
    r"for other uses|this article is about|"
    r"people with the name|people named)",
    re.I,
)
#: A numbered sense list ("… 1) an English saxophonist … 2) the former bassist …").
#: Two or more enumerations inside the opening is the shape, not one stray "1)".
_BIO_ENUMERATED = re.compile(r"(?:^|[\s:;,.])(\d)[)\].]\s+\S")
#: Any pre-1900 year in the opening of the prose. Birth dates live in the first
#: sentence, and Wikipedia exposes no birth_year field at all — so this is the
#: only signal that caught 'Reverend Arthur Meagher Cave (1883-1948)' and
#: 'Joel Hinman (January 27, 1802 - ...)' arriving as "modern" replacements.
#: Deliberately strict: a genuine modern bio rarely opens with a 19th-century
#: year, and a false reject only costs a gap, never a wrong biography.
_BIO_OLD_BIRTH = re.compile(r"\b1[6-8]\d{2}\b")

#: Providers that index RECORDING ARTISTS only. Prose from these is wrong for a
#: crew credit (director/producer/writer) often enough to be unusable: they return
#: a same-name musician who passes a casual glance. This is the skill's
#: "never run crew gaps through musician-only sources", enforced in code.
MUSIC_ONLY_SOURCES = frozenset({"lastfm", "genius", "theaudiodb", "deezer"})

CREW_TYPES = frozenset({"director", "producer", "writer", "creator", "composer"})

#: Occupations that cannot be a film CREW credit. Proven live: TMDB answered a
#: Director credit with "George Scott was a Canadian professional wrestler, booker
#: and promoter" and scored it `ready`.
#:
#: Matched only in a DEFINING construction ("<name> was a … wrestler"), not by bare
#: keyword: a documentary director's opening line often says "her film followed a
#: retired boxer", and a keyword scan flagged exactly that. The copula plus at most
#: four intervening words is what separates "he was a wrestler" from "she filmed a
#: wrestler". Musician is deliberately absent — musicians direct music docs — and so
#: is actor, which is what most documentary subjects are.
_INCOMPATIBLE_WITH_CREW = re.compile(
    r"\b(?:is|was)\s+(?:(?:an?|the)\s+)?(?:[\w-]+\s+){0,4}"
    r"(wrestler|footballer|soccer player|cricketer|boxer|jockey|swimmer|sprinter|"
    r"athlete|darts player|snooker player|golfer|politician|member of parliament|"
    r"senator|congressman|congresswoman|bishop|archbishop|priest|clergyman|"
    r"naval officer|admiral|general|soldier)\b"
    # The occupation must END its phrase. Without this, the adjective forms match:
    # "was a general manager" and "was a general practitioner" were both flagged as
    # military generals. "was a wrestler, booker and promoter" still ends a phrase.
    r"(?=\s*(?:[.,;)]|$|\b(?:in|of|for|who|and)\b))",
    re.I,
)


def profession_conflict(text: str, credit_type: str) -> Optional[str]:
    """A crew credit carrying a life from an unrelated profession.

    Only the opening is read, and only a defining sentence counts: a director's bio
    can mention a boxer they filmed without being one.
    """
    if (credit_type or "").strip().lower() not in CREW_TYPES:
        return None
    hit = _INCOMPATIBLE_WITH_CREW.search((text or "")[:260])
    return f"profession-conflict-for-crew:{hit.group(1).lower()}" if hit else None


def bio_kind(text: str) -> str:
    """Classify a candidate biography: biography | sense-list | too-short."""
    t = (text or "").strip()
    if len(t) < 40:
        return "too-short"
    opening = t[:400]
    if _BIO_SENSE_LIST.search(opening):
        return "sense-list"
    # Two or more enumerations in the opening = a list of senses, not a life.
    if len(set(_BIO_ENUMERATED.findall(opening))) >= 2:
        return "sense-list"
    return "biography"


def bio_implausible(text: str) -> Optional[str]:
    """Why this text must not be used as a biography, or None if it is fine."""
    kind = bio_kind(text)
    if kind == "too-short":
        return "too-short"
    if kind == "sense-list":
        return "junk-multiartist-or-disambiguation"
    if _BIO_OLD_BIRTH.search((text or "")[:400]):
        return "pre-1900-birth-in-text"
    return None


def collision_reason(text: str, *, credit_type: str = "", source: Optional[str] = None) -> Optional[str]:
    """Extra reason a candidate must not be auto-applied, beyond `bio_implausible`.

    Covers the case where the text is a fine biography *of somebody else*, which no
    regex can see. The reliable signal is provenance: a musician-only provider
    answering a crew credit has returned a different human, almost by construction.

    NOTE the honest limit: a coherent wrong-person biography of a genuinely
    musical person is NOT detectable here. Last.fm's "Peter Grant … British singer
    … debut album New Vintage" is a well-formed life about the wrong Peter Grant
    (Led Zeppelin's manager died in 1995). Those still need the identity gate and
    a human read; do not pretend this function catches them.
    """
    # Evaluated once: the reason is the same either way and re-running the regexes
    # doubles the work on every candidate in the plan.
    reason = bio_implausible(text)
    if reason:
        return reason
    if source in MUSIC_ONLY_SOURCES and (credit_type or "").strip().lower() in CREW_TYPES:
        return f"crew-credit-from-music-source:{source}"
    conflict = profession_conflict(text, credit_type)
    if conflict:
        return conflict
    return None


def _name_ok(query: str, found: str, aliases=None) -> bool:
    """Is `found` plausibly the same subject as `query`?

    Provider search is fuzzy by design, so a record must not be trusted just
    because it came back. Proven live: Deezer answers 'Nick Banks' with
    'Nickelback' — a nickname mismatch that would have put a Nickelback photo on
    the Pulp drummer's row.

    `aliases` are documented alternate names (MusicBrainz), which is how a real
    alias like 'William Hammond Talbot' passes while a lookalike still fails.
    """
    import difflib

    from .. import matcher

    def sq(v):
        # Strip parenthetical disambiguators before comparing: Wikipedia and
        # Genius routinely answer 'The Edge (Guitarist)' for 'The Edge', and
        # treating that as a different person is a false reject.
        import re as _re

        base = _re.sub(r"\([^)]*\)", " ", v or "")
        return matcher.squash(matcher.clean_release(base))

    q, f = sq(query), sq(found)
    if not q or not f:
        return False
    if q == f:
        return True
    if any(sq(a) == f for a in (aliases or [])):
        return True
    ratio = difflib.SequenceMatcher(None, q, f).ratio()
    if ratio >= 0.86:
        return True
    short, long_ = sorted((q, f), key=len)
    return short in long_ and len(short) / max(1, len(long_)) >= 0.6


def resolve(
    name: str, kind: str = "person", providers: Optional[List[Provider]] = None
) -> Dict[str, Any]:
    """Search, fetch details, and merge with per-field provenance."""
    providers = providers if providers is not None else default_providers()
    found = search(name, kind, providers)
    by_name = {p.name: p for p in providers}
    records: List[Record] = []
    rejected: List[Dict[str, Any]] = []
    # Identity first: MusicBrainz supplies documented aliases and an MBID for
    # the gate below. Identity-only — it never supplies prose.
    identity: Dict[str, Any] = {}
    mb = next((p for p in providers if p.name == "musicbrainz"), None)
    mb_ident = getattr(mb, "identity", None)
    if callable(mb_ident):
        try:
            raw = mb_ident(name) or {}
            identity = raw if isinstance(raw, dict) else {}
        except Exception:  # noqa: BLE001
            identity = {}
    aliases = identity.get("aliases") or []
    # ONE candidate per provider: fetching every fuzzy hit makes unrelated
    # artists (The Kills, The Chicks for 'The Chills') part of the merge and
    # floods the conflict list. Best hit only, highest score first.
    best_per_provider: Dict[str, Candidate] = {}
    for cand in found["_candidates"]:
        if cand.provider not in best_per_provider:
            best_per_provider[cand.provider] = cand
    for provider_name, cand in best_per_provider.items():
        provider = by_name.get(provider_name)
        if not provider:
            continue
        try:
            rec = provider.fetch(cand)
        except cache.ProviderError as exc:
            found["errors"][provider.name] = str(exc)[:160]
            continue
        except Exception as exc:  # noqa: BLE001
            found["errors"][provider.name] = f"{type(exc).__name__}: {str(exc)[:120]}"
            continue
        if rec:
            found_name = str(rec.fields.get("name") or "")
            if not _name_ok(name, found_name, aliases):
                # Fuzzy search answers are not identities. Drop the record
                # rather than let a wrong bio or photo into the merge.
                rejected.append(
                    {
                        "provider": provider_name,
                        "name": found_name,
                        "reason": "name-mismatch",
                    }
                )
                continue
            records.append(rec)

    # Reject biographies that are junk or describe a pre-1900 life. Portraits
    # from the same record are kept — an image can still be right when the prose
    # is not, and portraits are byte-verified separately.
    for r in records:
        bio = r.fields.get("biography")
        if not bio:
            continue
        why = bio_implausible(bio)
        if why:
            r.fields.pop("biography", None)
            r.provenance.pop("biography", None)
            rejected.append(
                {"provider": r.provider, "name": r.fields.get("name"), "reason": why}
            )

    plausible, suspect = _split_plausible(records)
    for r in suspect:
        if plausible:  # younger candidate exists -> drop
            rejected.append(
                {
                    "provider": r.provider,
                    "name": r.fields.get("name"),
                    "reason": "birth-year-pre-1900",
                    "birth_year": _birth_year(r),
                }
            )
    use = plausible or suspect  # pre-1900 only as a last resort
    merged = merge(use, PROSE_PRIORITY, PORTRAIT_PRIORITY)
    # A name 'conflict' between the query and a documented alias is not a
    # conflict: 'William Hammond Talbot' IS Billy Talbot.
    conflicts = dict(merged["conflicts"])
    if "name" in conflicts and aliases:
        values = [v.get("value") for v in conflicts["name"]]
        if all(_name_ok(name, str(v), aliases) for v in values if v):
            conflicts.pop("name")
    out = {
        "query": name,
        "kind": kind,
        "fields": merged["fields"],
        "provenance": merged["provenance"],
        "conflicts": conflicts,
        "rejected": rejected,
        "identity": identity,
        "birth_year": merged["fields"].get("birth_year"),
        "birth_suspect": bool(suspect and not plausible),
        "sources": [r.to_dict() for r in records],
        "errors": found["errors"],
        "skipped": found["skipped"],
        "bio_len": len(str(merged["fields"].get("biography") or "")),
    }
    return out
