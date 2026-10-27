"""Kodi/Jellyfin .nfo sidecars — metadata that survives DB row recreation.

Review-P2.3: repaired Name/Overview/ProviderIds live only in Jellyfin's
SQLite. A Tdarr container swap or folder move drops the row and wipes the
locks. A `<lockdata>true</lockdata>` sidecar next to the file re-applies
them automatically. Builders are pure (tested); only write_sidecar touches
the media host, via base64 over the existing ssh helper (no quoting trap).
"""

from __future__ import annotations

import base64
import shlex
from xml.sax.saxutils import escape


def episode_nfo(title: str, plot: str, premiered: str = "", tmdb_id: str = "",
                imdb_id: str = "") -> str:
    """Kodi <episodedetails> document. Pure."""
    ids = ""
    if tmdb_id:
        ids += f'\n    <uniqueid type="tmdb" default="true">{escape(tmdb_id)}</uniqueid>'
    if imdb_id:
        ids += f'\n    <uniqueid type="imdb">{escape(imdb_id)}</uniqueid>'
    aired = f"\n    <premiered>{escape(premiered)}</premiered>" if premiered else ""
    return ("""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<episodedetails>
    <title>{t}</title>
    <plot>{p}</plot>{aired}{ids}
    <lockdata>true</lockdata>
</episodedetails>
""".format(t=escape(title), p=escape(plot), aired=aired, ids=ids))


def movie_nfo(title: str, plot: str, year: str = "", tmdb_id: str = "",
              imdb_id: str = "") -> str:
    """Kodi <movie> document. Pure."""
    ids = ""
    if tmdb_id:
        ids += f'\n    <uniqueid type="tmdb" default="true">{escape(tmdb_id)}</uniqueid>'
    if imdb_id:
        ids += f'\n    <uniqueid type="imdb">{escape(imdb_id)}</uniqueid>'
    yr = f"\n    <year>{escape(year)}</year>" if year else ""
    return ("""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<movie>
    <title>{t}</title>
    <plot>{p}</plot>{yr}{ids}
    <lockdata>true</lockdata>
</movie>
""".format(t=escape(title), p=escape(plot), yr=yr, ids=ids))


def sidecar_for(media_disk_path: str) -> str:
    """`<stem>.nfo` next to the media file. Pure."""
    stem = media_disk_path.rsplit(".", 1)[0]
    return f"{stem}.nfo"


def write_sidecar(media_disk_path: str, xml: str) -> bool:
    """Write the sidecar on the media host. Returns success; never raises."""
    from . import probe  # deferred: light import for tests

    try:
        b64 = base64.b64encode(xml.encode("utf-8")).decode("ascii")
        probe.on_media(
            f"echo {shlex.quote(b64)} | base64 -d > {shlex.quote(sidecar_for(media_disk_path))}"
        )
        return True
    except Exception:  # noqa: BLE001 - sidecar is a bonus, never a gate
        return False
