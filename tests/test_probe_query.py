"""probe.query(): a question is not an action, so a non-zero exit is an answer."""
import subprocess

from media_lib import probe


class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def test_non_zero_is_returned_not_raised(monkeypatch):
    """`[ -d X ]` exits 1 to mean "no"; on_media() raised on that and crashed the gate."""
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: _Proc(1, ""))
    assert probe.query("[ -d /nope ]") == (1, "")


def test_output_is_passed_through(monkeypatch):
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: _Proc(0, "yes\n"))
    assert probe.query("[ -d /x ]") == (0, "yes\n")


def test_timeout_is_unknown_not_an_exception(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=1)

    monkeypatch.setattr(probe.subprocess, "run", boom)
    assert probe.query("sleep 999") == (-1, "")


def test_unreachable_host_is_unknown(monkeypatch):
    def boom(*a, **k):
        raise OSError("no route to host")

    monkeypatch.setattr(probe.subprocess, "run", boom)
    assert probe.query("ls") == (-1, "")
