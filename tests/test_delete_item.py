"""delete_item must refuse container items — it deletes media, not just rows."""
import pytest

from media_lib import jellyfin


@pytest.mark.parametrize("itype", ["Series", "Season", "Folder", "BoxSet"])
def test_refuses_container_items(monkeypatch, itype):
    monkeypatch.setattr(jellyfin, "get_json",
                        lambda *a, **k: {"Type": itype, "Name": "Some Title"})
    called = []
    monkeypatch.setattr(jellyfin, "_request", lambda *a, **k: called.append(a))

    with pytest.raises(ValueError) as exc:
        jellyfin.delete_item("abc123")

    assert "unlinks every media file" in str(exc.value)
    assert called == [], "must not reach the server"


def test_allows_single_file_rows(monkeypatch):
    monkeypatch.setattr(jellyfin, "get_json", lambda *a, **k: {"Type": "Episode", "Name": "E1"})
    called = []
    monkeypatch.setattr(jellyfin, "_request", lambda *a, **k: called.append(a))

    assert jellyfin.delete_item("abc123") == 204
    assert len(called) == 1


def test_container_override_is_explicit(monkeypatch):
    monkeypatch.setattr(jellyfin, "get_json", lambda *a, **k: {"Type": "Series", "Name": "X"})
    called = []
    monkeypatch.setattr(jellyfin, "_request", lambda *a, **k: called.append(a))

    assert jellyfin.delete_item("abc123", allow_container=True) == 204
    assert len(called) == 1


def test_failed_lookup_does_not_block_single_file_delete(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("item not found")

    monkeypatch.setattr(jellyfin, "get_json", boom)
    called = []
    monkeypatch.setattr(jellyfin, "_request", lambda *a, **k: called.append(a))

    assert jellyfin.delete_item("abc123") == 204
    assert len(called) == 1
