"""xvid_tdarr_batch pure core: parse/filter/group (fake api, no network)."""
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

import xvid_tdarr_batch as b  # noqa: E402


def test_parse_audit(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("mpeg4,1,2,3|DocuSeries/x.avi\nbad line\n\n"
                 "mpeg2,1,2,N/A|Films/y.mkv\n")
    assert b.parse_audit(f) == ["DocuSeries/x.avi", "Films/y.mkv"]


def test_video_only_drops_sidecars():
    video, non = b.video_only(["a/x.avi", "a/x.nfo", "a/cover.jpg",
                               "a/y.MKV", "a/z.sample"])
    assert video == ["a/x.avi", "a/y.MKV"]
    assert sorted(non) == ["a/cover.jpg", "a/x.nfo", "a/z.sample"]


def test_receipt_done(tmp_path):
    r = tmp_path / "r.jsonl"
    r.write_text(json.dumps({"path": "a", "status": "SWAPPED"}) + "\n"
                 + json.dumps({"path": "b", "status": "GATE-FAIL"}) + "\n"
                 + "not json\n"
                 + json.dumps({"path": "c", "status": "SKIP-DONE"}) + "\n")
    assert b.receipt_done(r) == {"a", "c"}


def test_receipt_missing_is_empty(tmp_path):
    assert b.receipt_done(tmp_path / "nope.jsonl") == set()


def test_drop_job_handled_matches_basename():
    paths = ["DocuSeries/x.avi", "Films/y.mkv"]
    jobs = ["/media/videos/DocuSeries/x.avi"]  # Tdarr _id form
    assert b.drop_job_handled(paths, jobs) == ["Films/y.mkv"]


def test_group_by_library():
    g = b.group_by_library(["/media/videos/DocuSeries/a.avi",
                            "/media/videos/DocuSeries/b.avi",
                            "/media/videos/Films/c.mkv"])
    assert sorted(g) == ["DocuSeries", "Films"]
    assert len(g["DocuSeries"]) == 2


def test_plan_mode_readonly(tmp_path, monkeypatch):
    import tdarr_kick
    a = tmp_path / "a.txt"
    a.write_text("mpeg4,1,2,3|DocuSeries/x.avi\n"
                 "mpeg4,1,2,3|DocuSeries/x.nfo\n")
    r = tmp_path / "r.jsonl"
    r.write_text("")
    monkeypatch.setattr(tdarr_kick, "crud", lambda *x, **k: (_ for _ in ()).throw(AssertionError("no api")))
    # crud must not be reached: job history failure is tolerated, records
    # failure is advisory — but here force both to raise to prove plan lives
    rc = b.main(["--audit", str(a), "--receipt", str(r)])
    assert rc == 0
