"""fix_aspect_remux pure parse (no network)."""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from fix_aspect_remux import parse_suspects  # noqa: E402


def test_parse_suspects(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("ok: 1  unknown: 0  suspects: 1\n"
                 "  [suspect-narrow] 720x400 DAR=5:4 Some Film\n"
                 "        /data/docuseries/Some Film/Some Film.avi\n"
                 "        wide raster 720x400 (1.80) flagged DAR 5:4\n")
    assert parse_suspects(str(f)) == [
        ("/data/docuseries/Some Film/Some Film.avi", "720x400", "Some Film")]
