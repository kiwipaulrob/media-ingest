"""sab.watch — fixtures, no network."""

import sys

sys.path.insert(0, ".")

from media_lib import sab as sabmod  # noqa: E402


def test_watch_returns_completed_job(monkeypatch):
    calls = {"n": 0}

    def fake_queue():
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"nzo_id": "NZO1", "filename": "Show.S01E01.mkv",
                     "status": "Downloading", "percentage": "42"}]
        return []

    monkeypatch.setattr(sabmod, "queue", fake_queue)
    monkeypatch.setattr(sabmod, "history", lambda limit=10: [
        {"nzo_id": "NZO1", "name": "Show.S01E01.mkv",
         "status": "Completed", "fail_message": "", "size": "123"}])
    out = sabmod.watch("NZO1", timeout_s=30, poll_s=0)
    assert out == {"done": True, "status": "Completed", "fail_message": "",
                   "size": "123", "name": "Show.S01E01.mkv"}


def test_watch_returns_failure_message(monkeypatch):
    monkeypatch.setattr(sabmod, "queue", lambda: [])
    monkeypatch.setattr(sabmod, "history", lambda limit=10: [
        {"nzo_id": "NZO9", "name": "Old.Post.2018.mkv",
         "status": "Failed", "fail_message": "Aborted, cannot complete",
         "size": "35 MB"}])
    out = sabmod.watch("old.post", timeout_s=30, poll_s=0)
    assert out["done"] is True
    assert out["status"] == "Failed"
    assert "Aborted" in out["fail_message"]


def test_watch_times_out_on_stuck_job(monkeypatch):
    monkeypatch.setattr(sabmod, "queue", lambda: [
        {"nzo_id": "NZO2", "filename": "Stuck.mkv",
         "status": "Downloading", "percentage": "1"}])
    monkeypatch.setattr(sabmod, "history", lambda limit=10: [])
    out = sabmod.watch("NZO2", timeout_s=1, poll_s=1)
    assert out == {"done": False, "status": "timeout",
                   "fail_message": "still queued after 1s",
                   "size": None, "name": "Stuck.mkv"}
