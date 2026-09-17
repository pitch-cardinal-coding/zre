"""zre — Pure Python ZRE (RFC 36) implementation."""

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

__version__ = "0.1.2"
