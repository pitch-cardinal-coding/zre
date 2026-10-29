"""zre — Pure Python ZRE (RFC 36) implementation."""

# Single source of truth for the package version: pyproject.toml reads
# this attribute dynamically ([tool.setuptools.dynamic]). Bump only this
# line, then tag v<same>. The release workflow refuses a tag/artifact
# mismatch.
__version__ = "0.3.0"

from .gossip import GossipHub
from .node import Codec, Group, Peer, UUIDCollisionError, ZreError, ZreNode

__all__ = [
    "Codec",
    "GossipHub",
    "Group",
    "Peer",
    "UUIDCollisionError",
    "ZreError",
    "ZreNode",
]
