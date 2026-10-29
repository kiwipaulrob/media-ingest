# Adopting external code reviews

How suggestions from Gemini (Jules / AI Studio / Code Assist) become code here.

The short version: **a review is input, not a patch.** Every item is triaged
against the incident history in `README.md`, implemented with a test, and
recorded below with the reason it was adopted or rejected.

## Where the code runs (this is why adoption is immediate)

The Hermes plugin executes **this checkout**, and the profile links into it:

```
profile/plugins/media-ingest -> media-ops/plugins/media-ingest   (symlink, not a copy)
plugins/media-ingest/tools.py:  MEDIA_OPS = /root/.hermes/profiles/media-maintainer/media-ops
                                subprocess.run([... '-m', 'cli.<stage>'], cwd=MEDIA_OPS)
```

So there is no build or deploy step — a change merged to `master` here is what
the next tool call runs. Two consequences:

1. **Test before merge**, always. `python3 -m pytest tests/ -q` (464 tests), and
   CI (`.github/workflows/tests.yml`) runs the same suite on the PR.
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

Start with `README.md` § "Beyond ingest" for the four newer verbs and their test
status — `multipart_to_series.py` and `migrate_series_between_libs.py` carry guards
but no dedicated tests yet, which is the highest-value review target in the tree.

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
| 1 | 2026-10-21 | Jules (PR #2) | `black` reformat across 33 files | **Adopt** | Proven behaviour-neutral: AST identical in 41/51 files; the 10 semantic changes are unused-import removals, annotation-only edits, or the guarded `re.search` in `place.py`. 147 tests green on the branch and after merge. | `48f9642` |
| 2 | 2026-10-21 | Jules (PR #2) | mypy annotations (`Optional[str]` on `strip_html`, `norm_name`; `dict[str, Any]` on `minimal`) | **Adopt** | Annotation-only; no runtime effect. Correct: those functions do accept `None`. | `48f9642` |
| 3 | 2026-10-21 | Jules (PR #2) | Removed `jellyfin`/`config` from `cli/meta.py`, `safety` from `enrich.py`, `json` from `chain.py`, `Path` from `repair.py`, `matcher` from `source.py` | **Adopt** | Each verified unreferenced by AST name analysis *and* by importing all 34 modules. One stale docstring resulted (fixed in `4f0d1c0`). | `48f9642` |
| 4 | 2026-10-21 | Jules (PR #2) | Removed `rejected: List[Dict[str, Any]] = []` in `enrich.py` | **Adopt** | Dead declaration — `rejected` appears later only as a keyword argument name, not a variable. | `48f9642` |
| 5 | 2026-10-21 | Jules (PR #2) | Extracted `re.search(r'COPIED=(\w+)', stage)` into `copied_match` in `place.py` | **Adopt** | Independently identical to the Optional-handling fix made earlier; strictly safer. | `48f9642` |

## Deferred — under review (decided, not rejected)

Items judged sound but not worth doing yet. Each carries the **evidence that would
reopen it**, so it is not relitigated from scratch.

### Packaging `media-ops` (`pyproject.toml`, `pip install -e .`, import instead of `subprocess`)

**Verdict: Defer (2026-10-22). Keep under review.**

The plugin shells out (`subprocess.run([... '-m', 'cli.<stage>'], cwd=MEDIA_OPS)`)
rather than importing `media_lib`. Packaging would remove the hardcoded path, make
errors real exceptions instead of parseable strings, let mypy follow the imports,
and let tests drive the entry point in-process.

**Why deferred:** the strongest argument for packaging was eliminating two copies
of the code, and the **symlink already solved that** at a fraction of the risk. The
remaining benefits do not currently justify the costs:

- **It would not have prevented either bug this session.** The duplicate
  `wait_rows` block and the false "row MISSING" report were *behaviour* defects
  that a green 147-test suite passed straight through. Packaging buys ergonomics,
  not correctness.
- **The process boundary is load-bearing.** A crash or hang in `cli.scan` currently
  kills a subprocess and returns an exit code. Imported, it can take the **gateway**
  down — a real availability regression for long copies and hung HTTP calls.
- **It would break "changes apply on the next tool call."** An imported `media_lib`
  is frozen at gateway start, so a `.env` or code change would need a restart —
  losing a property the whole workflow depends on.
- `subprocess` gives timeouts and cancellation for free; in-process needs threads
  or signals.

**Reopen if any of these becomes true:**

1. A plugin/`media_lib` version skew causes a real failure (the plugin running
   against an incompatible `media_lib`).
2. A second consumer of `media_lib` appears — the package boundary starts paying.
3. Subprocess startup becomes material to a workflow (measure first; it is ~0.3 s
   per call today).
4. Type checking across the boundary is wanted for a change that is otherwise
   unsafe to make.
5. The checkout stops being the live code (a real deploy step appears), which
   removes the freshness argument in its favour.

**Narrower step available instead** (not taken; no trigger met): keep the
subprocess, but have `tools.py` import `media_lib.config` for paths and a version
check, so the plugin refuses to run against a mismatched `media_lib` instead of
failing mid-placement. Reversible, and it captures the one benefit with a real
incident behind it (skew), not a hypothetical.

## How to hand a review over

- **As text** — paste it into chat. It gets triaged item by item.
- **As a PR** — say the number; `gh pr checkout <n>` brings it in for diff review,
  and accepted hunks are cherry-picked rather than merged whole.
- **As a file** — drop a `.md`/`.txt` anywhere and name the path.
