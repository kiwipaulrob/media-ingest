"""Watch documentary festival programme pages for newly added titles.

Two of the highest-value sources have no usable feed: Doc'n Roll (music-doc
festival, returns 404 on /feed) and Doc Edge (NZ, 403 behind Cloudflare on any
feed path). Both render their programme as server-side HTML though, so the
reliable approach is to parse the page, keep a snapshot of the titles seen, and
report the difference on the next run.

Printed output is deliberately one line per new title so it can be piped into a
digest without further parsing.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / "state" / "doc-discovery-seen.json"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

SOURCES = {
    "Doc'n Roll": "https://www.docnrollfestival.com/films/",
    "Doc Edge": "https://docedge.nz/films/",
}


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return fh.read().decode("utf-8", "replace")


# Screening blocks repeat a film once per screening, decorated with the Q&A guest,
# a sold-out banner, or an encore marker. Those decorations are not new films, so
# they are stripped before comparison — otherwise every added screening looks like
# a new discovery.
_DECOR = re.compile(r"\s*\([^)]*(?:Q&A|SOLD OUT|FINAL TIX|ENCORE|GUEST TBC)[^)]*\)\s*$",
                    re.I)


def clean(title: str) -> str:
    t = htmllib.unescape(title)
    t = re.sub(r"\*+", "", t)
    # A screening can carry several decorations at once, e.g.
    # "(*** FINAL TIX ***) (DIRECTOR Q&A)" — strip until nothing changes.
    while True:
        stripped = _DECOR.sub("", t)
        if stripped == t:
            break
        t = stripped
    return re.sub(r"\s+", " ", t).strip(" -–—")


def key(title: str) -> str:
    """Punctuation-insensitive key: 'KING OF THEM ALL – THE STORY' == slug form."""
    return re.sub(r"[^a-z0-9]", "", htmllib.unescape(title).lower())


def titles_docnroll(html: str) -> set[str]:
    out = set()
    for m in re.finditer(r'<h2[^>]*>(.*?)</h2>', html, re.S | re.I):
        t = re.sub(r"<[^>]+>", "", m.group(1))
        t = re.sub(r"\s+", " ", t).strip()
        # screening blocks repeat the film title; keep only clean, film-like strings
        t = clean(t)
        if 3 < len(t) < 90 and not t.lower().startswith(("view by", "shorts:")):
            out.add(t)
    # href slugs are a fallback for films with no h2 — but a slug is lowercase, so
    # only keep it when no properly cased title already covers it.
    for m in re.finditer(r'href="/films/([a-z0-9\-]+)/"', html, re.I):
        slug = m.group(1).replace("-", " ")
        if not any(key(slug) == key(t) for t in out):
            out.add(slug.title())
    return {t.strip() for t in out if t.strip()}


def titles_docedge(html: str) -> set[str]:
    out = set()
    for m in re.finditer(r'<h3[^>]*>(.*?)</h3>', html, re.S | re.I):
        t = re.sub(r"<[^>]+>", "", m.group(1))
        t = clean(t)
        if 3 < len(t) < 90:
            out.add(t)
    return {t for t in out if t.strip()}


PARSERS = {"Doc'n Roll": titles_docnroll, "Doc Edge": titles_docedge}


def main() -> int:
    seen_all = json.loads(STATE.read_text()) if STATE.exists() else {}
    changed = False
    total_new = 0

    for name, url in SOURCES.items():
        try:
            html = fetch(url)
            found = PARSERS[name](html)
        except Exception as exc:  # noqa: BLE001 - a dead source must not kill the sweep
            print(f"! {name}: fetch/parse failed: {exc}", flush=True)
            continue

        if not found:
            print(f"! {name}: parsed zero titles — page shape may have changed", flush=True)
            continue

        seen = set(seen_all.get(name, []))
        new = sorted(found - seen)
        if new:
            changed = True
            total_new += len(new)
            print(f"\n{name}: {len(new)} new / {len(found)} total")
            for t in new:
                print(f"  + {t}")
        else:
            print(f"{name}: no change ({len(found)} titles)")

        seen_all[name] = sorted(seen | found)

    if changed:
        seen_all["_updated"] = date.today().isoformat()
        STATE.write_text(json.dumps(seen_all, indent=2, sort_keys=True))
    print(f"\nnew titles this run: {total_new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
