"""Two-way sync guards.

The checkout these commands mutate IS the running code, so the tests focus on
the refusals rather than the happy path:

  * push must refuse when local is behind origin (that push would need a merge);
  * push must refuse a red test suite;
  * pull must refuse a dirty tree (a merge over uncommitted work is unrecoverable);
  * a failed test run after a merge must revert the merge, not leave it half-on.
"""
from cli import sync


def _patch_git(monkeypatch, table):
    """Route `git <args...>` through a table keyed on the first two args.

    Default is success EXCEPT for `rev-parse --verify`, which drives the
    "merge in progress" guard: defaulting that to success made every command
    abort before reaching the guard under test.
    """
    calls = []
    defaults = {"rev-parse --verify": (1, ""), "branch --show-current": (0, "master"),
                "rev-parse --short": (0, "abc1234")}

    def fake_git(*args, check=False):
        calls.append(args)
        key = " ".join(args[:2])
        for pattern, result in table.items():
            if key == pattern or args[0] == pattern:
                return result
        return defaults.get(key, (0, ""))

    monkeypatch.setattr(sync, "git", fake_git)
    return calls


def test_push_refuses_when_local_is_behind(monkeypatch, capsys):
    _patch_git(monkeypatch, {"fetch --quiet": (0, ""),
                             "rev-list --left-right": (0, "3\t1")})
    monkeypatch.setattr(sync, "tests", lambda: (True, "150 passed"))
    rc = sync.cmd_push(type("A", (), {"message": "x"})())
    assert rc == 2
    assert "behind origin" in capsys.readouterr().out


def test_push_refuses_a_red_suite(monkeypatch, capsys):
    _patch_git(monkeypatch, {"fetch --quiet": (0, ""),
                             "rev-list --left-right": (0, "0\t1")})
    monkeypatch.setattr(sync, "tests", lambda: (False, "1 failed, 149 passed"))
    rc = sync.cmd_push(type("A", (), {"message": "x"})())
    assert rc == 1
    assert "red" in capsys.readouterr().out


def test_pull_refuses_a_dirty_tree(monkeypatch, capsys):
    _patch_git(monkeypatch, {"fetch --quiet": (0, ""),
                             "rev-list --left-right": (0, "2\t0"),
                             "status --porcelain": (0, " M cli/scan.py")})
    rc = sync.cmd_pull(type("A", (), {})())
    assert rc == 2
    assert "uncommitted" in capsys.readouterr().out


def test_pull_reverts_the_merge_when_tests_fail(monkeypatch, capsys):
    calls = _patch_git(monkeypatch, {"fetch --quiet": (0, ""),
                                     "rev-list --left-right": (0, "2\t0"),
                                     "status --porcelain": (0, ""),
                                     "merge --no-edit": (0, "merged")})
    monkeypatch.setattr(sync, "tests", lambda: (False, "2 failed"))
    rc = sync.cmd_pull(type("A", (), {})())
    assert rc == 1
    assert ("reset", "--hard", "ORIG_HEAD") in calls, "a failing merge must be undone"
    assert "reverted" in capsys.readouterr().out


def test_merge_pr_refuses_with_uncommitted_work(monkeypatch, capsys):
    _patch_git(monkeypatch, {"status --porcelain": (0, "?? notes.txt")})
    rc = sync.cmd_merge_pr(type("A", (), {"number": 3, "keep_branch": False})())
    assert rc == 2
    assert "stash" in capsys.readouterr().out


def test_repo_slug_parses_both_remote_forms(monkeypatch):
    for url in ("https://github.com/kiwipaulrob/media-ingest.git",
                "git@github.com:kiwipaulrob/media-ingest.git",
                "https://github.com/kiwipaulrob/media-ingest"):
        monkeypatch.setattr(sync, "git", lambda *a, **k: (0, url))
        assert sync.repo_slug() == "kiwipaulrob/media-ingest"


def test_plugin_must_be_a_symlink_into_the_repo(monkeypatch, tmp_path):
    """A real directory means the old copy drift is back: edits stop reaching GitHub."""
    installed = tmp_path / "plugins" / "media-ingest"
    installed.mkdir(parents=True)
    (installed / "plugin.yaml").write_text("name: media-ingest\n")
    monkeypatch.setattr(sync, "INSTALLED_PLUGIN", installed)
    monkeypatch.setattr(sync, "REPO_PLUGIN", tmp_path / "repo" / "media-ingest")
    problems = sync.plugin_link_status()
    assert problems and "real directory" in problems[0]


def test_plugin_symlink_must_point_at_the_repo(monkeypatch, tmp_path):
    repo_plugin = tmp_path / "repo" / "media-ingest"
    repo_plugin.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    installed = tmp_path / "plugins" / "media-ingest"
    installed.parent.mkdir(parents=True)
    installed.symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setattr(sync, "INSTALLED_PLUGIN", installed)
    monkeypatch.setattr(sync, "REPO_PLUGIN", repo_plugin)
    problems = sync.plugin_link_status()
    assert problems and "expected" in problems[0]

    installed.unlink()
    installed.symlink_to(repo_plugin, target_is_directory=True)
    assert sync.plugin_link_status() == []


def test_broken_skill_link_is_reported(monkeypatch, tmp_path):
    skills = tmp_path / "skills" / "media"
    skills.mkdir(parents=True)
    good_target = tmp_path / "repo" / "good"
    good_target.mkdir(parents=True)
    (good_target / "SKILL.md").write_text("---\nname: good\n---\n")
    (skills / "good").symlink_to(good_target, target_is_directory=True)
    (skills / "broken").symlink_to(tmp_path / "gone", target_is_directory=True)
    monkeypatch.setattr(sync, "SKILLS_INSTALLED", tmp_path / "skills")
    problems = sync.skill_link_status()
    assert len(problems) == 1 and "broken" in problems[0]


def test_a_broken_workflow_yaml_is_caught_before_push(monkeypatch, tmp_path):
    """GitHub runs a malformed workflow with ZERO jobs, so CI stops silently."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "good.yml").write_text("on: push\njobs:\n  a:\n    steps: []\n")
    monkeypatch.setattr(sync, "REPO", tmp_path)
    assert sync.workflow_problems() == []

    (wf / "bad.yml").write_text("on: push\njobs:\n  a:\n    run: |\nbroken at col 0\n")
    problems = sync.workflow_problems()
    assert problems and "bad.yml" in problems[0]


def test_push_refuses_when_a_workflow_is_broken(monkeypatch, capsys, tmp_path):
    _patch_git(monkeypatch, {"fetch --quiet": (0, ""),
                             "rev-list --left-right": (0, "0	1")})
    monkeypatch.setattr(sync, "tests", lambda: (True, "160 passed"))
    monkeypatch.setattr(sync, "workflow_problems", lambda: ["tests.yml: bad indent"])
    rc = sync.cmd_push(type("A", (), {"message": "x"})())
    assert rc == 1
    assert "zero jobs" in capsys.readouterr().out


def test_conflict_detection(monkeypatch):
    monkeypatch.setattr(sync, "git", lambda *a, **k: (0, "abc123"))
    assert sync.conflict_in_progress() is True
    monkeypatch.setattr(sync, "git", lambda *a, **k: (1, ""))
    assert sync.conflict_in_progress() is False
