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


def test_plugin_drift_detects_a_differing_file(monkeypatch, tmp_path):
    installed = tmp_path / "installed"; installed.mkdir()
    vendored = tmp_path / "vendored"; vendored.mkdir()
    monkeypatch.setattr(sync, "INSTALLED_PLUGIN", installed)
    monkeypatch.setattr(sync, "VENDORED_PLUGIN", vendored)
    monkeypatch.setattr(sync, "run", lambda cmd, cwd=None, check=False: (
        1, f"Files {installed}/tools.py and {vendored}/tools.py differ"))
    drift = sync.plugin_drift()
    assert len(drift) == 1 and "tools.py" in drift[0]


def test_conflict_detection(monkeypatch):
    monkeypatch.setattr(sync, "git", lambda *a, **k: (0, "abc123"))
    assert sync.conflict_in_progress() is True
    monkeypatch.setattr(sync, "git", lambda *a, **k: (1, ""))
    assert sync.conflict_in_progress() is False
