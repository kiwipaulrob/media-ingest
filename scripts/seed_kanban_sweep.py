"""One-off: seed the global Kanban board from the memory/session/skill sweep.

Sources swept (2026-10-22):
  * this session's compaction summary (Blocked / Key Decisions sections)
  * retired Bot Chat 2026-09-26..10-19 (@session:media-maintainer/20260926_175843_801e41)
  * UPS session 2026-10-19, hot-water session 2026-10-21
  * media-ops README "Known weak spots", docs/REVIEWS.md "Deferred"
  * live checks: meta-plan.json (425/425 = done), hermes cron list, kanban.db

Only items verified as still open are included; anything already finished was
dropped (e.g. the 425-row metadata plan, which the sweep found complete).
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

TASKS: List[Dict[str, Any]] = [
    # ---------------------------------------------------------------- code
    dict(title="media-ops: keep packaging decision under review",
         priority=1, area="code",
         body="""DECIDED 2026-10-22: do NOT package media-ops (`pyproject.toml`, `pip install -e .`,
import instead of subprocess). Keep under review.

Reasoning + the five reopen conditions are recorded in `docs/REVIEWS.md` § Deferred
and summarised in README "Known weak spots".

Reopen if: (1) plugin/media_lib version skew causes a real failure; (2) a second
consumer of media_lib appears; (3) subprocess startup becomes material; (4) type
checking across the boundary is needed; (5) the checkout stops being the live code.

Narrower step available if trigger (1) fires: have tools.py import media_lib.config
for paths + a version check so the plugin refuses to run against a mismatched
media_lib, instead of failing mid-placement."""),

    dict(title="skill_manage cannot write through symlinked skills",
         priority=2, area="code",
         body="""The 12 repo-owned skills are symlinked from the profile into media-ops
(profile/skills/media/<name> -> media-ops/skills/media/<name>). Reads work
(skill_view, skills_list both find them), but `skill_manage(action='patch')`
returns "Skill not found in active profile".

Workaround in use: edit the repo file directly with the patch/write_file tools.

Options: (a) accept the workaround and document it; (b) revert those skills to
copies + a drift check in cli/sync.py; (c) check whether the loader can be made
to follow dir symlinks for the write path too.

Impact: low (workaround is fine), but skill edits lose skill_manage's lint pass."""),

    dict(title="cli/plan.py: propose a destination for NEW episodes",
         priority=2, area="code",
         body="""`cli/plan.py` still routes genuinely NEW episodes to the "needs your check"
list. A `NEW` decision row carrying a proposed destination would close the loop
and remove the hand-written placement spec.

Source: README "Known weak spots"."""),

    dict(title="Parallelise placements (2-3 workers)",
         priority=2, area="code",
         body="""Placements run sequentially. Copying is I/O-bound (~39 MB/s to CIFS) and the
gates are network-bound, so 2-3 workers should roughly double batch throughput.

Constraint: the gate order is size -> probe -> height -> duration -> decode, and a
failed gate must remove the copy before anything else touches the row. Keep that
ordering per item.

Source: README "Known weak spots"."""),

    dict(title="Correct COPY_MBPS (158 is ~4x optimistic)",
         priority=3, area="code",
         body="""`config.COPY_MBPS = 158` is a local-disk measurement and overstates this CIFS
path ~4x. Measured: 39 MB/s. Budget at ~40 MB/s so time estimates are honest.

Source: README "Known weak spots"."""),

    dict(title="Resume failed placements instead of re-copying from zero",
         priority=3, area="code",
         body="""A failed placement re-copies from zero; `.part` files are removed rather than
resumed. For multi-GB files over CIFS this wastes real time on a retry.

Source: README "Known weak spots"."""),

    dict(title="cli/meta.py: hardcoded library parent id should be a flag",
         priority=3, area="code",
         body="""`cli/meta.py` hardcodes one library's parent id. Make it a `--library` flag so
the metadata pass can target any library (Music Docos, DocuSeries, FilmDocos).

Source: README "Known weak spots"."""),

    dict(title="Provider cache has no eviction",
         priority=3, area="code",
         body="""`state/meta-cache` grows without bound (~2,626 entries at last count). Add a
size/age bound so it cannot grow forever.

Source: README "Known weak spots"."""),

    # ------------------------------------------------------------ jellyfin
    dict(title="Pre-1900 bio review: present the remaining 40 rows",
         priority=1, area="jellyfin",
         body="""A pre-1900 birth year is a strong wrong-person signal. After the guarded repair
(22 cleared, 1 replaced) the list went 63 -> 40 rows.

Outstanding: present those 40 one at a time with the CURRENT bio text visible and
CLICKABLE options (the user views on a phone, inline output only — no files).

Note: the earlier 40-row list contained regex false positives (matched any 4-digit
year, e.g. "born December 17, 1953" flagged via an unrelated "1600"). Birth-specific
regexes were derived; `state/pre1900-real.json` is the corrected set.

Files: state/pre1900-real.json, state/pre-1900-review.md, state/bio-repairs.jsonl"""),

    dict(title="Chris Rodley bio still empty (TMDB shell blank)",
         priority=2, area="jellyfin",
         body="""Person row for Chris Rodley (TMDB 1078679) has no biography — TMDB's shell is
empty, so this needs hand research (AudioCulture / NZ On Screen / MusicBrainz
identity + Wikidata). One of the top-8 high-value gaps.

Never clobber: write via full-DTO round-trip, lock Name+Overview after."""),

    dict(title="Hugh Sundae portrait still open",
         priority=3, area="jellyfin",
         body="""Hugh Sundae has a bio but no portrait. Portraits are separate from bios — a
wrong face is silent damage — so this needs explicit approval of the candidate
image before it is written.

Candidate sources: NZ On Screen profile, Wikimedia (check licence + identity via
MusicBrainz/Wikidata first)."""),

    dict(title="86 ID-less items: staged poster candidates await review",
         priority=2, area="jellyfin",
         body="""Three researchers were hunting art for the 86 ID-less items (frame-grab
placeholders). Staged candidates for the uncertain ones were to be brought back
for review and never auto-applied.

Outstanding: gather the staged candidates, present the unsure ones for approval,
apply the clear ones (poster.jpg beside media, or <basename>-poster.jpg for loose
files), verify by byte-comparing the served image."""),

    dict(title="Classic Albums: 10 titles absent from every source",
         priority=2, area="jellyfin",
         body="""Audited 73 Usenet Classic Albums postings; these 10 are absent everywhere
(Usenet + torrent indexes):

Carly Simon, Gil Scott-Heron, Judas Priest, Simply Red, Queen, Grateful Dead,
Deep Purple, Iron Maiden (Number of the Beast), Stevie Wonder, Paul Simon.

Decision needed: accept the absence and stop looking, or widen the search
(MVGroup forum, yt-dlp, other private trackers). Report the honest null rather
than re-searching blindly."""),

    dict(title="Lou Reed Transformer + Black Sabbath Paranoid: incomplete Usenet posts",
         priority=2, area="jellyfin",
         body="""Both grabs came up short at source (retried once, same shortfall: 1 and 5
blocks missing). `Repair failed, N short` gets exactly ONE retry — a second
failure means source-incomplete.

Outstanding: find SD/alternative copies, or drop the titles. Do not keep
re-grabbing the same incomplete post (retries spend block data)."""),

    dict(title="Rush 2112: only 1of2 available on Usenet",
         priority=3, area="jellyfin",
         body="""The grabbed Rush *2112* release is `1of2` and there is no `2of2` on Usenet.
A partial release is usually a different release from what is already in the
library, so do not present it as a clean replacement.

Outstanding: find part 2, find a complete alternative, or leave it."""),

    dict(title="Later with Jools Holland: are there unscanned files on disk?",
         priority=2, area="jellyfin",
         body="""The series row holds 58 episodes while 501 episode rows carry its name; 443
rows have NO file path (virtual placeholders — the user chose to keep them, so
that part is closed).

Still open: the subagent offered to check whether media files exist on disk that
Jellyfin has not scanned. Library holds seasons 4, 27-41, 43, 44, 48 + Specials
versus TMDB's 501 — so a disk-vs-library audit is the way to find genuinely
missing files rather than trusting the placeholder count.

Method: list the series folders on CT105, compare against rows with a non-empty
Path. Never a library-wide rescan (purges deliberately-kept ghost rows)."""),

    dict(title="Oasis 1080p upgrade watch: Saturday cron is gone",
         priority=2, area="jellyfin",
         body="""The retired session reported an "Oasis 1080p upgrade watch" running on
Saturdays. `hermes cron list` for media-maintainer now shows only three jobs
(nightly recode [paused], dashboard watchdog, hot-water report) — no Oasis watch.

Outstanding: decide whether to recreate it (a weekly sourcing check for a higher
resolution Oasis documentary) or confirm it was dropped deliberately.

Related watch item: Nowhere Inn — still no better copy found."""),

    dict(title="Alias noise: MusicBrainz alias check before bulk writes",
         priority=3, area="jellyfin",
         body="""Residual alias noise exists in the bio/alias queues. MusicBrainz is integrated
as an identity/alias provider (MBID, country, disambiguation, aliases) and never
as prose.

Outstanding: run the MB alias check over the residual candidates before any bulk
write, so a same-name person is not merged in. Rejected candidates go to the
review queue, never guessed."""),

    # --------------------------------------------------------------- infra
    dict(title="UPS service: dry_run still ON — arm live when instructed",
         priority=2, area="infra", assignee="hermes-maintainer",
         body="""From the 19 Oct session: "Do not power down servers" was noted; nothing was
changed (dry_run still True, engine ONLINE, no shutdown triggered). The user said
"I will do dry run later" and chose to keep dry_run ON with health + autostart
verification only.

Outstanding: arm live shutdown behaviour when the user says so. Until then, no
shutdown tests."""),

    dict(title="Hot-water controller: decision pending + tempering-valve check",
         priority=3, area="infra", assignee="hermes-maintainer",
         body="""Measured value of a tank-constrained controller is ~$60/yr (not the earlier
$119-136 upper bound — the optimiser wrongly treated the cylinder as an unlimited
battery). Payback: worth it as a cheap Zigbee module (~1.3 yr at $80), hard to
justify as a $400 electrical job.

Open follow-up: if the cylinder thermostat can be raised to 60-65 C with a
tempering valve, the usable buffer grows from ~1.7 kWh to ~4-5 kWh and afternoon
solar banking becomes real — roughly doubles the controller's value. Worth
checking when the electrician is next there."""),

    dict(title="Nightly software recode cron is paused — confirm intent",
         priority=3, area="infra",
         body="""`hermes cron list` shows job 4be2d96f208e "Nightly software recode 11pm-5am" as
[paused], last run 2026-10-05.

This matches the standing instruction "do not restart scrub unless instructed",
so it is likely deliberate — confirm rather than resume silently."""),
]


def main() -> int:
    created = []
    for t in TASKS:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(str(t["body"]))
            path = fh.name
        cmd = ["hermes", "kanban", "create", t["title"],
               "--body-file", path,
               "--priority", str(t.get("priority", 0)),
               "--created-by", "media-maintainer",
               "--json"]
        if t.get("assignee"):
            cmd += ["--assignee", t["assignee"]]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd="/root")
        Path(path).unlink(missing_ok=True)
        if proc.returncode != 0:
            print(f"FAILED  {t['title']}: {proc.stderr.strip()[:160]}")
            continue
        try:
            data = json.loads(proc.stdout)
            tid = data.get("id") or data.get("task", {}).get("id")
        except json.JSONDecodeError:
            tid = proc.stdout.strip()[:20]
        created.append((tid, t["title"], t["area"]))
        print(f"OK  {tid}  [{t['area']}]  {t['title']}")

    print(f"\ncreated {len(created)}/{len(TASKS)}")
    return 0 if len(created) == len(TASKS) else 1


if __name__ == "__main__":
    sys.exit(main())
