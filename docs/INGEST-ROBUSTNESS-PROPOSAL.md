# Ingest Workflow: Robustness + Efficiency Proposal

Date: 2026-10-30. Status: PROPOSAL for external review. Nothing below is built yet.

## 1. Pain observed (this week, live)

| # | Failure | Cost |
|---|---|---|
| 1 | `cli.scan --out` chatter corrupts scan.json when stdout redirected (hit 30 Oct: plan crashed on JSONDecodeError) | wasted sweep, manual rerun |
| 2 | `cli.meta plan` killed by timeout leaves `state/plan.json` truncated (2 bytes, 0 rows) — no atomic write | silent empty plan |
| 3 | Wrong `--library` name fails after seconds with ValueError, no name list (hit twice: "Music", "Music Docs") | confusion, reruns |
| 4 | Hunt triage over 100–500-posting Prowlarr pools is hand-driven token filtering per session | agent time, repeated logic |
| 5 | Failure triage (stalls, dead swarms, mislabels) re-derived from numbers every time | agent time |
| 6 | Aspect/audio/legacy-codec detection needs an agent to remember to probe | gaps linger months |
| 7 | Ambiguous picks (matcher abstains) have no judge but the agent's gut | inconsistent calls |

## 2. Proposal A — Batch orchestrator (highest leverage)

One script, `scripts/ingest_batch.py`, chaining the existing tested pieces:

```
scan → plan → (human picks ids) → queue → settle → ingest --from-plan → verify → receipt
```

Rules (non-negotiable, carried over from current doctrine):

- Dry-run is the default; live writes need `--apply`.
- Queue takes explicit ids only; no "all" switch (existing `source.queue` contract).
- Every stage appends to a receipt log (`state/receipts/<date>.jsonl`): what ran, what decided, what wrote.
- Atomic state writes: write tmp + rename; never truncate plan.json on kill (fixes pain 2).
- `--library` validated against live `VirtualFolders` with "did you mean" (fixes pain 3).
- `cli.scan` separates chatter (stderr) from artefact (file only) (fixes pain 1).

## 3. Proposal B — Nightly flag-scans → morning pick-list (more Tdarr, less agent)

Cron (in-window aware, read-only at night, queues never):

- `scan_aspect_flags.py` (exists) + new `scan_audio_compat.py` (EAC3/DTS-only stereo-missing) + `reencode-sweep.py` legacy-codec (exists).
- Output: one pick-list with per-file `--files` queue commands ready for the morning human pick.
- `tdarr_queue.py --flow audio|sar|legacy --files ...` stays the only write path (lesson 42).

Agent probing for "anything need recoding" disappears; the list arrives every morning.

## 4. Proposal C — Jev-1.14 as tie-breaker only

Jev (TypeSafe decisions model, OpenRouter `typesafe/jev-1.14`) fits exactly one slot:
the abstained pick. Contract:

- Input: wanted title/year/runtime + 2–4 candidate postings (size, seeds, tokens) + matcher reason for abstaining.
- Output: verdict (pick A / pick none) with reasons; logged to receipt.
- Everything routine stays deterministic (`quality.decide`, `title_matches`); Jev never sees a case code can decide — cost without benefit otherwise.
- `scripts/judge_pick.py --wanted ... --candidates state/x.json [--apply]`; dry-run prints the brief it WOULD send.

Open question for reviewer: Jev-1.14 vs 1.13 versioning/pinning; cost per call; whether the Decisions API shape fits a 4-candidate brief.

## 5. What is explicitly NOT proposed

- No end-to-end auto-ingest without human picks (approval doctrine stands).
- No library-linked Tdarr flows (lesson 42 stands).
- No matcher threshold loosening (lessons 37/38 stand; Jev judges, it does not re-tune).
- No skill rewrites beyond indexing the new scripts.

## 6. Acceptance

- 30 Oct bugs (scan chatter, plan truncation, library validation) fixed with regression tests.
- Orchestrator dry-run over Music Docos produces a receipt with zero writes.
- One Jev-judged abstained pick logged, human-confirmed.
- Reviewer sign-off on Jev contract + cost.
