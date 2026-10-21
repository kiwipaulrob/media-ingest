# Adopting external code reviews

How suggestions from Gemini (Jules / AI Studio / Code Assist) become code here.

The short version: **a review is input, not a patch.** Every item is triaged
against the incident history in `README.md`, implemented with a test, and
recorded below with the reason it was adopted or rejected.

## Where the code runs (this is why adoption is immediate)

The Hermes plugin executes **this checkout**:

```
plugins/media-ingest/tools.py:  MEDIA_OPS = /root/.hermes/profiles/media-maintainer/media-ops
                                subprocess.run([... '-m', 'cli.<stage>'], cwd=MEDIA_OPS)
```

So there is no build or deploy step — a change merged to `master` here is what
the next tool call runs. Two consequences:

1. **Test before merge**, always. `python3 -m pytest tests/ -q` (147 tests).
2. A change to the **plugin's own** `tools.py` / `plugin.yaml` additionally needs
   a gateway restart, because the plugin is imported at start-up:
   `hermes -p default gateway restart`.

## The loop

```
Jules review ──▶ triage ──▶ branch ──▶ implement + test ──▶ tests green? ──▶ master ──▶ push
                    │                                                        │
                    └──▶ rejected? record why in this file ───────────────────┘
```

**1. Triage.** Each item is classified:

| Verdict | Meaning |
|---|---|
| **Adopt** | Real defect or clear improvement. Implement with a test. |
| **Adopt differently** | Real problem, better fix than proposed — say so. |
| **Reject (invariant)** | Contradicts a rule bought with an incident. Cite it. |
| **Reject (wrong)** | The reviewer misread the code; quote the lines. |
| **Defer** | Valid but not now; add it to "Known weak spots" in the README. |

**2. Never adopt blind.** A suggestion that a reviewer cannot point at a
function for is a hypothesis. If it cannot be reproduced, it is not a defect.

**3. One item = one commit**, with the reviewer's point in the message. That
keeps `git revert` surgical.

**4. Tests are the gate, not the reviewer's confidence.** New behaviour needs a
new test. A fix with no test is not done.

## Invariants a reviewer will trip over (reject with these)

These look like improvements and are not. Each has an incident behind it.

- "Just rescan the library to pick up the new files" — a full rescan purges
  deliberately-kept ghost rows. `notify_media` exists for this.
- "Higher seed counts mean the release is healthy" — 63 and 95 advertised seeds
  both delivered zero bytes. Seeds are a floor, never evidence.
- "Retry the failed download again" — `Repair failed, N short` gets exactly one
  retry; a second failure means source-incomplete, and retries spend block data.
- "Speed it up by copying in parallel inside `place_new`" — the gate order is
  size → probe → height → duration → decode, and a failed gate must remove the
  copy before anything else touches the row.
- "Trust the provider's episode number" — TMDB numbering diverged from the
  library and wrote two wrong synopses. Resolve episodes **by name**.
- "Use the audio provider's prose as a fallback" — TheAudioDB prose is blocked
  structurally; it once returned a 2,000-character bio for the wrong band.

## Decision log

| # | Date | Source | Item | Verdict | Rationale | Commit |
|---|---|---|---|---|---|---|
| — | — | — | *(awaiting first review)* | — | — | — |

## How to hand a review over

- **As text** — paste it into chat. It gets triaged item by item.
- **As a PR** — say the number; `gh pr checkout <n>` brings it in for diff review,
  and accepted hunks are cherry-picked rather than merged whole.
- **As a file** — drop a `.md`/`.txt` anywhere and name the path.
