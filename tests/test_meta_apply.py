"""cli.meta apply — the library gate.

A plan built for one library must never be applied under another: apply reads
the per-library plan for --library and refuses (exit 2, zero writes) when the
plan's stamped library disagrees. Legacy plans with no stamp still apply.
"""
import argparse
import json
from contextlib import nullcontext

from cli import meta

LIB_A = "a" * 32
LIB_B = "b" * 32


def _args(lib, plan_path):
    return argparse.Namespace(library=lib, plan=str(plan_path),
                              ids="", limit=None)


def _plan(tmp_path, rows, library=LIB_A, stamp=True):
    blob = {"people_gaps": len(rows), "rows": rows}
    if stamp:
        blob["library"] = library
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(blob))
    return p


def _row(pid="p1"):
    return {"status": "ready", "id": pid, "name": "Some Person",
            "bio": "A fine musician. " * 10}


def test_apply_matching_library_writes(tmp_path, monkeypatch):
    wrote = []
    monkeypatch.setattr(meta.enrich, "apply_person",
                        lambda pid, bio, src: wrote.append(pid) or
                        {"status": "DONE", "chars": 100})
    monkeypatch.setattr(meta.safety, "acquire",
                        lambda *a, **k: nullcontext())
    rc = meta._apply(_args(LIB_A, _plan(tmp_path, [_row()])))
    assert rc == 0 and wrote == ["p1"]


def test_apply_mismatched_library_refuses_without_writing(tmp_path,
                                                           monkeypatch):
    wrote = []
    monkeypatch.setattr(meta.enrich, "apply_person",
                        lambda pid, bio, src: wrote.append(pid) or
                        {"status": "DONE", "chars": 100})
    monkeypatch.setattr(meta.safety, "acquire",
                        lambda *a, **k: nullcontext())
    rc = meta._apply(_args(LIB_B, _plan(tmp_path, [_row()])))
    assert rc == 2 and wrote == []


def test_apply_unstamped_legacy_plan_still_applies(tmp_path, monkeypatch):
    wrote = []
    monkeypatch.setattr(meta.enrich, "apply_person",
                        lambda pid, bio, src: wrote.append(pid) or
                        {"status": "DONE", "chars": 100})
    monkeypatch.setattr(meta.safety, "acquire",
                        lambda *a, **k: nullcontext())
    rc = meta._apply(_args(LIB_A, _plan(tmp_path, [_row()], stamp=False)))
    assert rc == 0 and wrote == ["p1"]
