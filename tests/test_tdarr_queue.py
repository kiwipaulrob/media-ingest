"""tdarr_queue payload + refusal logic (fake api, no network)."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

import tdarr_queue  # noqa: E402


def test_payload_shape():
    p = tdarr_queue.queue_payload("/media/a/x.mkv", "j2PAq5X")
    assert p["fileIds"] == ["/media/a/x.mkv"]
    assert p["updatedObj"]["flowId"] == "j2PAq5X"
    assert p["updatedObj"]["TranscodeDecisionMaker"] == "Queued"
    assert p["updatedObj"]["bumped"] is True


def test_flows_known():
    assert set(tdarr_queue.FLOWS) == {"audio", "sar", "legacy"}


def test_unknown_files_refuse(monkeypatch):
    import tdarr_kick
    monkeypatch.setattr(tdarr_kick, "crud", lambda *a, **k: [])
    rc = tdarr_queue.main(["--flow", "audio", "--files", "/media/nope.mkv", "--apply"])
    assert rc == 2


def test_dry_run_queues_nothing(monkeypatch):
    import tdarr_kick
    monkeypatch.setattr(tdarr_kick, "crud",
                        lambda *a, **k: [{"_id": "/media/a.mkv"}])
    calls = []
    rc = tdarr_queue.main(["--flow", "sar", "--files", "/media/a.mkv"],
                          call_api=lambda *a, **k: calls.append(1) or {})
    assert rc == 0 and calls == []
