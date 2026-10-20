"""Metadata provider chain (read-only).

    from media_lib.providers import chain
    chain.search('The Chills')      # candidates + per-provider errors
    chain.resolve('Martin Phillipps')  # merged fields + provenance

Prose priority   tmdb > lastfm > genius > wikipedia
Portrait priority tmdb > deezer > lastfm > wikipedia
"""
from . import chain
from .base import Candidate, Provider, Record, merge

__all__ = ['chain', 'Candidate', 'Provider', 'Record', 'merge']
