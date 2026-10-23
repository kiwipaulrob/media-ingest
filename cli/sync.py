"""Two-way sync between this checkout (the live code) and the GitHub repo.

The rule that shapes everything here: **this directory is what runs.** The plugin
shells out to `cli.*` in place, so a merge takes effect immediately and a broken
merge breaks the homelab. Therefore every mutating command runs the test suite
first and rolls back if it fails.

  python3 -m cli.sync status          # where do local and GitHub disagree?
  python3 -m cli.sync push -m "msg"   # commit + test + push (local -> GitHub)
  python3 -m cli.sync pull            # test + merge origin/master (GitHub -> local)
  python3 -m cli.sync prs             # open PRs
  python3 -m cli.sync merge-pr N      # merge a PR locally, test, push, delete branch

Refuses to force-push, refuses to auto-resolve conflicts, and never merges with a
red test suite. `status` also reports plugin drift: the installed plugin and the
copy vendored into this repo are separate files and must not silently diverge.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
PROFILE = Path("/root/.hermes/profiles/media-maintainer")
INSTALLED_PLUGIN = PROFILE / "plugins" / "media-ingest"
REPO_PLUGIN = REPO / "plugins" / "media-ingest"
SKILLS_INSTALLED = PROFILE / "skills"
SKILLS_REPO = REPO / "skills"


def run(cmd: List[str], cwd: Optional[Path] = None, check: bool = False) -> Tuple[int, str]:
    """Run a command, returning (rc, combined output). Never raises on failure."""
    proc = subprocess.run(cmd, cwd=str(cwd or REPO), capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({proc.returncode}): {out.strip()[:300]}")
    return proc.returncode, out.strip()


def git(*args: str, check: bool = False) -> Tuple[int, str]:
    return run(["git", *args], check=check)


def gh(*args: str) -> Tuple[int, str]:
    return run(["gh", *args])


def tests() -> Tuple[bool, str]:
    """The gate. Nothing mutating proceeds on a red suite."""
    rc, out = run([sys.executable, "-m", "pytest", "tests/", "-q"])
    return rc == 0, out.strip().splitlines()[-1] if out.strip() else "no output"


def workflow_problems() -> List[str]:
    """Workflow YAML that will not parse.

    A malformed workflow does not fail loudly: GitHub creates a run with ZERO
    jobs, so the check silently never happens. That is how a stray unindented
    line in a `run: |` block reached master and looked like a test failure.
    """
    problems: List[str] = []
    wf_dir = REPO / ".github" / "workflows"
    if not wf_dir.is_dir():
        return problems
    try:
        import yaml
    except ImportError:
        # Never silently "all clear": an unvalidatable workflow is the exact
        # state that produced a zero-job run. Say so and let the caller decide.
        return ["(cannot validate: pyyaml not installed — pip install -r requirements-dev.txt)"]
    for wf in sorted(wf_dir.glob("*.y*ml")):
        try:
            yaml.safe_load(wf.read_text())
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{wf.name}: {exc}")
    return problems


def repo_slug() -> str:
    """owner/repo parsed from the origin remote — never hardcode it."""
    rc, out = git("remote", "get-url", "origin")
    if rc != 0 or not out:
        return ""
    url = out.strip()
    if url.startswith("git@") and ":" in url:
        url = url.split(":", 1)[1]
    elif "://" in url:
        url = url.split("://", 1)[1].split("/", 1)[1]
    return url[:-4] if url.endswith(".git") else url


def conflict_in_progress() -> bool:
    rc, _ = git("rev-parse", "--verify", "MERGE_HEAD")
    return rc == 0


def plugin_link_status() -> List[str]:
    """Problems with the installed plugin <-> repo wiring. Empty list means healthy.

    The plugin used to be a real directory with a second copy vendored into the
    repo, so the two could silently diverge (they did). It is now a symlink into
    the repo: one source of truth, and drift is structurally impossible. A plain
    directory here means someone restored a copy and edits will stop reaching
    GitHub.
    """
    problems: List[str] = []
    if not INSTALLED_PLUGIN.exists():
        return [f"{INSTALLED_PLUGIN} does not exist"]
    if not INSTALLED_PLUGIN.is_symlink():
        return [f"{INSTALLED_PLUGIN} is a real directory, not a symlink — "
                f"edits there will not reach GitHub"]
    target = Path(os.path.realpath(INSTALLED_PLUGIN))
    if target != Path(os.path.realpath(REPO_PLUGIN)):
        problems.append(f"plugin symlink -> {target}, expected {REPO_PLUGIN}")
    return problems


def skill_link_status() -> List[str]:
    """Repo-owned skills must be symlinked from the profile, and every link must resolve."""
    problems: List[str] = []
    for link in sorted(SKILLS_INSTALLED.rglob("*")):
        if not link.is_symlink():
            continue
        if not (link / "SKILL.md").exists():
            problems.append(f"broken skill link: {link}")
    return problems


# ------------------------------------------------------------------- status
def cmd_status(args) -> int:
    git("fetch", "--quiet")
    _, branch = git("branch", "--show-current")
    _, head = git("rev-parse", "--short", "HEAD")
    _, dirty = git("status", "--porcelain")
    _, counts = git("rev-list", "--left-right", "--count", "origin/master...HEAD")

    behind = ahead = "0"
    if counts and "\t" in counts:
        behind, ahead = counts.split("\t")

    print(f"live code: {REPO}")
    print(f"branch {branch or '(detached)'} at {head}")
    print(f"vs origin/master: ahead {ahead}, behind {behind}")

    dirty_files = [row for row in dirty.splitlines() if row.strip()]
    print(f"working tree: {'clean' if not dirty_files else f'{len(dirty_files)} uncommitted'}")
    for line in dirty_files[:10]:
        print("   ", line)

    if ahead != "0" and behind != "0":
        print("!! DIVERGED — resolve before pushing (pull first, then push)")

    problems = plugin_link_status() + skill_link_status()
    if problems:
        print("\n!! WIRING PROBLEMS:")
        for problem in problems[:8]:
            print("   ", problem)
    else:
        print("\nplugin + skills: symlinked into the repo (single source of truth)")

    rc, prs = gh("pr", "list", "--state", "open",
                 "--json", "number,title,author,headRefName",
                 "--jq", '.[] | "#\\(.number) \\(.author.login): \\(.title)"')
    print("\nopen PRs:", prs or "none")
    if count_open_prs() and behind == "0":
        print("   (PRs are not merged into local until you run: sync merge-pr N)")
    return 0


def count_open_prs() -> int:
    rc, out = gh("pr", "list", "--state", "open", "--json", "number", "--jq", "length")
    try:
        return int(out.strip() or "0")
    except ValueError:
        return 0


# --------------------------------------------------------------------- push
def cmd_push(args) -> int:
    if conflict_in_progress():
        print("refusing: a merge is in progress — resolve it first")
        return 2
    git("fetch", "--quiet")
    _, counts = git("rev-list", "--left-right", "--count", "origin/master...HEAD")
    behind = counts.split("\t")[0] if "\t" in counts else "0"
    if behind != "0":
        print(f"refusing: local is {behind} commit(s) behind origin — run `sync pull` first")
        return 2

    ok, summary = tests()
    print(f"tests: {summary}")
    if not ok:
        print("refusing to push: test suite is red")
        return 1

    wf_problems = workflow_problems()
    if wf_problems:
        print("refusing to push: workflow YAML will not parse "
              "(GitHub runs it with zero jobs, so CI silently stops):")
        for problem in wf_problems:
            print("   ", problem)
        return 1

    git("add", "-A")
    _, staged = git("diff", "--cached", "--name-only")
    if staged:
        if not args.message:
            print("nothing committed: pass -m \"message\" to commit the staged changes")
            print(staged)
            return 2
        rc, out = git("commit", "-m", args.message)
        if rc != 0:
            print("commit failed:", out[:300])
            return 1
        print("committed:", out.splitlines()[0] if out else "(ok)")
    else:
        print("nothing to commit; pushing existing commits")

    rc, out = git("push", "origin", "master")
    if rc != 0:
        print("push failed:", out[:300])
        return 1
    print("pushed to origin/master")
    return 0


# --------------------------------------------------------------------- pull
def cmd_pull(args) -> int:
    if conflict_in_progress():
        print("refusing: a merge is already in progress")
        return 2
    git("fetch", "--quiet")
    _, counts = git("rev-list", "--left-right", "--count", "origin/master...HEAD")
    behind, ahead = counts.split("\t") if "\t" in counts else ("0", "0")
    if behind == "0":
        print("already up to date with origin/master")
        return 0

    _, dirty = git("status", "--porcelain")
    if dirty.strip():
        print("refusing: working tree has uncommitted changes — commit or stash first")
        return 2

    rc, out = git("merge", "--no-edit", "origin/master")
    if rc != 0:
        if conflict_in_progress():
            git("merge", "--abort")
            print("merge conflicted — aborted, local untouched. Resolve manually.")
        else:
            print("merge failed:", out[:300])
        return 1

    ok, summary = tests()
    print(f"tests after merge: {summary}")
    if not ok:
        git("reset", "--hard", "ORIG_HEAD")
        print("merge reverted: the incoming changes fail the test suite")
        return 1
    print(f"merged {behind} commit(s) from origin/master; tests green")
    return 0


# ---------------------------------------------------------------------- PRs
def cmd_prs(args) -> int:
    rc, out = gh("pr", "list", "--state", "open", "--json",
                 "number,title,author,headRefName,additions,deletions,changedFiles",
                 "--jq", '.[] | "#\\(.number)  +\\(.additions)/-\\(.deletions)  \\(.changedFiles)f  '
                         '\\(.author.login)  \\(.headRefName)\\n     \\(.title)"')
    print(out or "no open PRs")
    return 0


def cmd_merge_pr(args) -> int:
    if conflict_in_progress():
        print("refusing: a merge is already in progress")
        return 2
    _, dirty = git("status", "--porcelain")
    if dirty.strip():
        print("refusing: commit or stash first (merging over uncommitted work is unsafe)")
        return 2

    rc, meta = gh("pr", "view", str(args.number), "--json",
                  "headRefName,title,state", "--jq",
                  '"\\(.headRefName)|\\(.title)|\\(.state)"')
    if rc != 0:
        print("cannot read PR:", meta[:200])
        return 1
    branch, title, state = (meta.split("|") + ["", "", ""])[:3]
    if state != "OPEN":
        print(f"PR #{args.number} is {state}, nothing to merge")
        return 2

    rc, _ = git("fetch", "--quiet", "origin", branch)
    if rc != 0:
        print("cannot fetch PR branch:", branch)
        return 1

    rc, out = git("merge", "--no-ff", "-m",
                  f"merge: {title} (PR #{args.number})", f"origin/{branch}")
    if rc != 0:
        if conflict_in_progress():
            git("merge", "--abort")
            print("merge conflicted — aborted, local untouched")
        else:
            print("merge failed:", out[:300])
        return 1
    print(f"merged PR #{args.number} locally")

    ok, summary = tests()
    print(f"tests after merge: {summary}")
    if not ok:
        git("reset", "--hard", "ORIG_HEAD")
        print("merge reverted: the PR fails the test suite, local left on master as it was")
        return 1
    wf_problems = workflow_problems()
    if wf_problems:
        git("reset", "--hard", "ORIG_HEAD")
        print("merge reverted: the PR breaks a workflow YAML: " + "; ".join(wf_problems))
        return 1

    rc, out = git("push", "origin", "master")
    if rc != 0:
        print("push failed (merge kept locally):", out[:200])
        return 1
    print("pushed to origin/master")

    if not args.keep_branch:
        slug = repo_slug()
        if slug:
            rc, out = gh("api", "-X", "DELETE", f"repos/{slug}/git/refs/heads/{branch}")
            print(f"deleted remote branch {branch}" if rc == 0
                  else f"branch not deleted ({out[:80]})")
        else:
            print("could not determine repo slug; branch left in place")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Two-way sync: live checkout <-> GitHub")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="where do local and GitHub disagree?")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("push", help="test, commit and push (local -> GitHub)")
    p.add_argument("-m", "--message", default="")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("pull", help="test and merge origin/master (GitHub -> local)")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("prs", help="list open PRs")
    p.set_defaults(func=cmd_prs)

    p = sub.add_parser("merge-pr", help="merge a PR locally, test, push, delete branch")
    p.add_argument("number", type=int)
    p.add_argument("--keep-branch", action="store_true")
    p.set_defaults(func=cmd_merge_pr)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
