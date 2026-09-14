"""zre — Pure Python ZRE (RFC 36) implementation."""

from .node import Codec, Group, Peer, UUIDCollisionError, ZreError, ZreNode

__all__ = [
    "Codec",
    "Group",
    "Peer",
    "UUIDCollisionError",
    "ZreError",
    "ZreNode",
]

__version__ = "0.1.2"
__all__ = ["Codec", "Group", "Peer", "ZreNode"]
