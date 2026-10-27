"""Fleet-health checks (the `admirarr doctor` idea, our stack's shape).

Read-only. One row per service: ok + detail. No repairs, no restarts —
anything red becomes a human decision, per the audit-over-reboot lesson.
"""

from __future__ import annotations

from typing import Any, Dict, List


def check() -> List[Dict[str, Any]]:
    """Probe Jellyfin, Prowlarr, SABnzbd, qBittorrent. Never raises."""
    from . import config, jellyfin, qb, sab  # deferred: light import for tests

    rows: List[Dict[str, Any]] = []

    def row(service: str, ok: bool, detail: str) -> None:
        rows.append({"service": service, "ok": ok, "detail": detail})

    try:
        libs: Any = jellyfin.get_json("/Library/VirtualFolders") or []
        row("jellyfin", True, f"{len(libs)} libraries")
    except Exception as e:  # noqa: BLE001 - health must not raise
        row("jellyfin", False, f"{type(e).__name__}")

    try:
        import urllib.request

        req = urllib.request.Request(
            f"{config.prowlarr_url().rstrip('/')}/api/v1/health",
            headers={"X-Api-Key": config.prowlarr_key()},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            row("prowlarr", r.status == 200, f"http {r.status}")
    except Exception as e:  # noqa: BLE001
        row("prowlarr", False, f"{type(e).__name__}")

    try:
        q = sab.queue()
        jobs = q.get("jobs", []) if isinstance(q, dict) else q
        row("sabnzbd", True, f"{len(jobs)} queued")
    except Exception as e:  # noqa: BLE001
        row("sabnzbd", False, f"{type(e).__name__}")

    try:
        ts = qb.torrents()
        active = [t for t in ts if str(t.get("state", "")).lower() not in ("pausedup", "stoppedup")]
        row("qbittorrent", True, f"{len(active)}/{len(ts)} active")
    except Exception as e:  # noqa: BLE001
        row("qbittorrent", False, f"{type(e).__name__}")

    return rows


def render(rows: List[Dict[str, Any]]) -> str:
    """Human-readable lines. Pure."""
    return "\n".join(
        f"{'ok ' if r['ok'] else 'FAIL'}  {r['service']:<12} {r['detail']}" for r in rows
    )
