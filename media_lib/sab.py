"""SABnzbd Web API client — the Usenet half of the fetch role.

Kept deliberately small: the sourcing stage only needs to queue an NZB by URL,
then report queue/history health. Completion is proved by reading the history
slot's ``status`` and ``storage`` path, never by an accepted ``nzo_id`` — the
API returns one of those for a grab that later fails at unpack time.

Endpoint shapes verified against SABnzbd 5.1.3:
  * queue/history answer ``{"queue"|"history": {"slots": [...]}}``
  * ``mode=addurl`` takes ``name=<url>`` and returns ``{"nzo_ids": [...]}``
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from . import config


class SabError(RuntimeError):
    pass


def _base() -> str:
    if not config.sab_url():
        raise SabError("SABNZBD_URL is not set")
    return config.sab_url().rstrip("/")


def _call(mode: str, **params: Any) -> Dict[str, Any]:
    qs = {"mode": mode, "output": "json", "apikey": config.sab_key()}
    qs.update({k: str(v) for k, v in params.items()})
    url = f"{_base()}/api?" + urllib.parse.urlencode(qs)
    try:
        with urllib.request.urlopen(url, timeout=90) as fh:
            # strict=False: release names carry unescaped control characters
            # (review-3); strict parsing crashed the whole queue read.
            return json.loads((fh.read() or b"{}").decode("utf-8", "replace"),
                              strict=False)
    except Exception as exc:  # noqa: BLE001
        raise SabError(f"{mode} failed: {type(exc).__name__}: {exc}") from exc


def queue() -> List[Dict[str, Any]]:
    return (_call("queue").get("queue") or {}).get("slots") or []


def history(limit: int = 20) -> List[Dict[str, Any]]:
    return (_call("history", limit=limit).get("history") or {}).get("slots") or []


def add_url(url: str, name: str = "", category: str = "movies") -> List[str]:
    """Queue an NZB by URL. Returns the new nzo_ids (empty when refused)."""
    out = _call("addurl", name=url, nzbname=name[:80], cat=category)
    return out.get("nzo_ids") or []


def completed_slots(limit: int = 30) -> List[Dict[str, Any]]:
    """History slots that finished AND carry a storage path on disk."""
    return [
        s for s in history(limit) if s.get("status") == "Completed" and s.get("storage")
    ]


def find_completed(name_contains: str, limit: int = 30) -> Optional[Dict[str, Any]]:
    needle = name_contains.lower()
    for slot in completed_slots(limit):
        if needle in (slot.get("name") or "").lower():
            return slot
    return None


_SHORT_RE = re.compile(r"(\d+)\s+short")
#: "Repair failed, N short" is worth exactly ONE retry when N is small; a large
#: N means the backbone simply lacks the articles and no retry will fix it.
RETRY_MAX_SHORT = 10


def retryable_failures(limit: int = 20) -> List[Dict[str, Any]]:
    """Failed slots that are worth one retry (small parity shortfall + a file)."""
    out = []
    for slot in history(limit):
        if slot.get("status") != "Failed":
            continue
        m = _SHORT_RE.search(slot.get("fail_message") or "")
        if not m:
            continue
        if int(m.group(1)) > RETRY_MAX_SHORT:
            continue
        out.append(
            {
                "nzo_id": slot.get("nzo_id"),
                "name": slot.get("name"),
                "short": int(m.group(1)),
                "size": slot.get("size"),
                "storage": slot.get("storage"),
            }
        )
    return out


def retry(nzo_id: str) -> bool:
    """Re-queue one history slot. Each retry spends block data."""
    return bool(_call("retry", value=nzo_id).get("status"))

# Failure classes. The distinction matters because the actions are opposite:
# a small parity shortfall is worth one retry, while a large one means the
# articles are absent from EVERY configured provider and no amount of retrying
# (or credit) will ever complete it — the release must be replaced, not retried.
REPAIR_SMALL = 10       # <=10 blocks short: one retry is worth the data
REPAIR_MEDIUM = 100     # 11-100: usually dead, but a second provider may fill it
DEAD_SHORT_RE = re.compile(r"\((\d+)\s+short\)")


def classify_failure(message: str, job: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Bucket a SAB failure so callers do not retry hopeless jobs.

    Learned the hard way (24 Oct 2026): 32 failed jobs were retried after a block
    account was topped up and every one failed identically. Credit never fixes a
    post whose articles are missing from the backbone.

    The sharpest signal is not the shortfall at all — it is `bytes == downloaded`:
    SAB fetched everything the providers would serve and THEN failed repair, which
    proves the missing articles do not exist upstream. A retry then re-downloads
    nothing and fails identically, whether it is 1 block short or 1595.
    """
    msg = str(message or "")
    fetched_all = False
    if isinstance(job, dict):
        try:
            got = int(job.get("downloaded") or 0)
            want = int(job.get("bytes") or 0)
            fetched_all = bool(want and got >= want)
        except (TypeError, ValueError):
            fetched_all = False
    result = _classify_by_message(msg)
    if fetched_all and result["retry_worthwhile"]:
        result = {"kind": "post-incomplete-at-provider",
                  "blocks_short": result.get("blocks_short"),
                  "retry_worthwhile": False}
    return result


def _classify_by_message(msg: str) -> Dict[str, Any]:
    m = DEAD_SHORT_RE.search(msg)
    if m:
        short = int(m.group(1))
        if short <= REPAIR_SMALL:
            kind = "retry"
        elif short <= REPAIR_MEDIUM:
            kind = "maybe"
        else:
            kind = "dead-post"
        return {"kind": kind, "blocks_short": short, "retry_worthwhile": short <= REPAIR_SMALL}
    low = msg.lower()
    if "corrupt rar" in low or "bad archive" in low or "failed to verify" in low:
        return {"kind": "damaged-post", "blocks_short": None, "retry_worthwhile": False}
    if "not on your server" in low or "cannot be completed" in low:
        return {"kind": "missing-from-provider", "blocks_short": None, "retry_worthwhile": False}
    return {"kind": "other", "blocks_short": None, "retry_worthwhile": False}
