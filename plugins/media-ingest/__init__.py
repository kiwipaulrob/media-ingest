"""media-ingest — Hermes plugin exposing the media ingest stage pipeline.

Design rules this plugin obeys:

* **No forked logic.** Every tool shells out to the `cli` stage that already
  exists, so the tests that protect the CLIs protect the agent path too.
* **Apply is id-gated.** `media_apply` requires a non-empty list of plan ids and
  refuses anything that is not a REPLACE row. There is no "do everything".
* **Read-only stays read-only.** scan and plan never change library state.
"""
from __future__ import annotations

import sys

MEDIA_OPS = '/root/.hermes/profiles/media-maintainer/media-ops'

#: Make media_lib/cli importable without installing into the gateway venv.
#: The repo is a working tree, not a published distribution, so a path shim is
#: the smallest thing that works and it is reversible in one line.
if MEDIA_OPS not in sys.path:
    sys.path.insert(0, MEDIA_OPS)

PLUGIN_NAME = 'media-ingest'


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin loader."""
    from .tools import register_tools
    register_tools(ctx)


__all__ = ['register', 'PLUGIN_NAME']
