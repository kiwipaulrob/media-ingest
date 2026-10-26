# Agent Review-and-Fix Playbook

How an external agent critically reviews this repo, and what happens to its feedback.

## The one rule

**Review-only: feedback as text. The maintaining agent implements, with tests.**
Do not open PRs, push branches, or commit code. The review loop in
`docs/REVIEWS.md` is the authority: every item is triaged, implemented with a
test, and recorded — or rejected with a cited reason.

## Read first (in this order)

1. `README.md` — architecture, module map, the four verbs (dedupe / merge /
   concat / series-convert), the invariants table.
2. `docs/REVIEWS.md` — how feedback becomes code, and the "invariants a reviewer
   will trip over" list (suggestions that look like improvements and are not).
3. `tests/` — 344 tests. Read the tests for any area before criticising it; they
   encode *why*, not just *what*.
4. The two guard-but-untested scripts — the highest-value review targets in the
   tree: `scripts/multipart_to_series.py`, `scripts/migrate_series_between_libs.py`.

## Feedback format (one item per line, so each maps to one commit)

```
[file:line] OBSERVATION — why it matters (defect / risk / simplification).
Repro or pointer: <exact function, input, or test that shows it>.
```

- A suggestion without a function pointer is a hypothesis, not a defect.
- If it cannot be reproduced, it is not a defect.
- One item = one line. Do not bundle five observations into one paragraph.

## What good feedback looks like here

- A guard with a hole (an input that passes it and should not).
- An untested path in a mutating stage (`cli/apply.py`, `cli/ingest_new.py`,
  `media_lib/swap.py`, `media_lib/place.py`).
- A provider-id or naming assumption that breaks on a real title
  (the `Metroland` class: same normalised title, different film).
- A race between a scan and a refresh (the library is eventually consistent).

## What will be rejected (with citation, per `docs/REVIEWS.md`)

- "Just rescan the library" — purges deliberately-kept ghost rows.
- "Trust seed counts / provider episode numbers / a 204 response" — each has an
  incident and a test.
- Style-only rewrites with no behaviour change — noise against the incident log.

## What happens next

The maintaining agent triages each item (Adopt / Adopt differently /
Reject / Defer), implements adoptions with tests, and records every verdict in
`docs/REVIEWS.md` § Decision log. Rejections cite the invariant or the lines
that show the misread. Nothing is adopted blind.
