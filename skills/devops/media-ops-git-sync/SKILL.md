---
name: media-ops-git-sync
description: "Use when syncing media-ops with GitHub either way."
version: 1.0.1
author: Paul Robertson
license: private
metadata:
  hermes:
    tags: [git, github, sync, media-ops, homelab]
    related_skills: [jellyfin-administration, media-torrent-hunting]
---

# media-ops: two-way sync with GitHub

Keep `/root/.hermes/profiles/media-maintainer/media-ops` (the LIVE code) and
`github.com/kiwipaulrob/media-ingest` (the published copy) in step, in both
directions, without ever breaking the running homelab.

## When to Use

- Code in `media-ops/` was edited and must reach GitHub.
- A PR was opened (by Jules, Gemini, or a human) and must reach the live checkout.
- `sync status` reports drift, divergence, or a dirty tree.
- The plugin behaves differently from the repo — suspect `vendor/` drift.
- Before any reorganisation: confirm nothing is pending in either direction.

## The rule everything follows

**The local checkout IS production.** The `media-ingest` plugin shells out to
`cli.*` in place:

```
plugins/media-ingest/tools.py:  MEDIA_OPS = /root/.hermes/profiles/media-maintainer/media-ops
```

So a merge takes effect on the next tool call, and a bad merge breaks ingest
immediately. GitHub is the mirror: review surface, PRs, backup, Pages. A merge
there changes nothing until it is pulled locally.

## One repo, and it is PUBLIC

`github.com/kiwipaulrob/media-ingest` is the only repository for this code, and it is
**public**. Two things make that easy to get wrong:

- **Never infer visibility from authentication.** `gh api` and `gh repo view` authenticate
  on every call, so "it needed my auth" says nothing about who can read it. Ask directly:

  ```bash
  gh repo view kiwipaulrob/media-ingest --json visibility,isFork,createdAt
  gh repo list kiwipaulrob --limit 100 --json name,visibility
  ```

- **The plugin and skills live INSIDE this repo and are symlinked into the profile.** The
  same code appears under `media-ops/` and under the profile's `plugins/` and `skills/`.
  That is one repo in two filesystem locations — not two repos.

Because everything pushed here is world-readable:

- Credentials never live in tracked files; they are read from the environment
  (`media_lib/config.py`). Check before pushing:
  `git grep -nIE "(api[_-]?key|token|secret|password)\s*[:=]"`.
- History is public too. The Tdarr key sat in commits `a09e1cf`, `e0d8d0e`, `4fa8d24`;
  deleting it from the working tree did not remove it from `git log -S`. **Rotate any secret
  that was ever committed** — treat it as published, because it is.
- To change visibility: `gh repo edit kiwipaulrob/media-ingest --visibility private`.

## Use the tool, not raw git

```bash
cd /root/.hermes/profiles/media-maintainer/media-ops
python3 -m cli.sync status            # where do local and GitHub disagree?
python3 -m cli.sync push -m "msg"     # local -> GitHub (tests first)
python3 -m cli.sync pull              # GitHub -> local (tests first, reverts on red)
python3 -m cli.sync prs               # open PRs with size
python3 -m cli.sync merge-pr 4         # merge PR locally, test, push, delete branch
```

Why not raw git: every mutating command here gates on the test suite, refuses a
dirty tree, refuses to push while behind origin, and **reverts a merge whose
tests fail** (`git reset --hard ORIG_HEAD`). Raw `git merge` leaves the live
tree broken.

## Direction 1 — local changes to GitHub

After editing `media_lib/`, `cli/` or `tests/`:

1. `python3 -m pytest tests/ -q` — must be green.
2. `python3 -m cli.sync push -m "<what changed and why>"`.

If push refuses with *"behind origin"*, someone changed GitHub first: run
`sync pull`, resolve, then push. Never force-push — history here is the audit
trail of what ran in production.

## Direction 2 — GitHub PRs to local

Jules (and other agents) open PRs against `master`; they do **not** reach the
homelab until merged locally.

1. `python3 -m cli.sync prs` — see what's open.
2. Review the diff before merging; a reformat PR is not a review.
3. `python3 -m cli.sync merge-pr N` — merges with `--no-ff`, runs tests, pushes,
   deletes the remote branch, and **reverts everything if tests fail**.

For a PR that is riskier than a test suite can prove (large reformat, provider
changes), merge on a branch first and inspect with an AST comparison:

```python
import ast
# ast.dump(ast.parse(a)) == ast.dump(ast.parse(b))  ->  cosmetic only
# otherwise ast.unparse both sides and diff: that shows SEMANTIC change only
```

This is how a +2102/-1298 "style & lint" PR was proven behaviour-neutral: 41 of
51 files had identical ASTs, and the 10 that differed were unused-import
removals, annotations, and one guarded `re.search`.

## Wiring: one source of truth per artifact

Nothing is copied any more — the profile *links into* the repo, so there is no
second copy to drift (the old `vendor/` copy did drift, and a stale test rode
along with it, still asserting four tools when six were registered):

```
/root/.hermes/profiles/media-maintainer/plugins/media-ingest -> media-ops/plugins/media-ingest
/root/.hermes/profiles/media-maintainer/skills/media/<name>  -> media-ops/skills/media/<name>
```

`sync status` reports **wiring problems** instead of drift: a real directory where
a symlink belongs, a link pointing elsewhere, or a broken skill link. If it says
"is a real directory", restore the link:

```bash
rm -rf /root/.hermes/profiles/media-maintainer/plugins/media-ingest
ln -s /root/.hermes/profiles/media-maintainer/media-ops/plugins/media-ingest \
      /root/.hermes/profiles/media-maintainer/plugins/media-ingest
hermes -p default gateway restart
```

Both Hermes loaders follow symlinks (`Path.is_dir()`), verified for the plugin
(`plugins list` shows it enabled) and for skills (a symlinked skill loads with
all its references).

**Only locally-authored skills live in the repo** (`media/*`,
`devops/media-ops-git-sync`, `ma-stream-health-checker`). Hub-installed skills are
tracked in `skills/.hub/lock.json` with content hashes — never move or symlink
those, the hub owns them.

Only `tools.py` / `plugin.yaml` changes need a gateway restart
(`hermes -p default gateway restart`) — `cli/`, `media_lib/` and skill changes
apply on the next tool call.

## CI runs the same suite on every PR

`.github/workflows/tests.yml` runs the suite on Python 3.12 and 3.14, the plugin
tests, and an import-every-module check, on both push and PR. That closes the
loop: previously a PR was "green" only because someone ran the tests locally
after fetching it. A PR that fails here is refused at the PR, not discovered in
production.

Keep the local gate too — CI never sees the real `.env`, the homelab network, or
the live Jellyfin server.

What the first CI runs actually caught (all real, none reproducible locally):

1. **A non-hermetic test.** It stat'ed the live `state/` dir and raised
   `PermissionError` on the runner before asserting anything. `config` now honours
   `MEDIA_OPS_HOME`, the test owns a `tmp_path` cache dir, and CI sets
   `MEDIA_OPS_HOME` under the workspace. **A test that reads a machine path is
   not a test.**
2. **A missing dependency in a test** (`import yaml`) — hence
   `requirements-dev.txt`; the runtime needs neither pytest nor pyyaml.
3. **A workflow YAML that would not parse.** GitHub creates a run with **ZERO
   jobs**, so CI stops checking everything while looking like a test failure.
   `sync push` now validates every workflow file first and refuses.
4. **A guard that passed silently when it could not validate** (pyyaml absent →
   `return []`). It now reports "cannot validate" instead of an all-clear.

Rule from all four: **when a check cannot run, it must say so.** A green tick
from a check that never executed is worse than no check.

Before pushing anything touching `.github/workflows/`, parse it locally:

```bash
python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/tests.yml')); print('valid')"
```

## Conflicts

`sync` aborts a conflicted merge and leaves local untouched. Resolve by hand:
merge the PR branch, fix, `pytest`, commit, push. Do not let a half-resolved
merge sit in the live tree — the plugin will execute it.

## Pre-flight before any reorganisation

- `sync status` clean, `sync prs` empty or understood.
- Tests green locally.
- Know that the first commit after a big reformat resets `git blame` — say so
  before doing it rather than after.
