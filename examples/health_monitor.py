#!/usr/bin/env python3
"""
Peer Health Monitor — Cluster health and failure detection

Monitors peer health, tracks EVASIVE/EXIT events, and provides cluster status.

Usage:
  python health_monitor.py <node_name> [--port PORT]
"""

import argparse
import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum

from zre import ZreNode


class HealthStatus(Enum):
    HEALTHY = "healthy"
    EVASIVE = "evasive"
    DEAD = "dead"


@dataclass
class PeerHealth:
    peer_id: str
    name: str
    status: HealthStatus = HealthStatus.HEALTHY
    last_seen: float = field(default_factory=time.time)
    evasive_count: int = 0
    groups: set[str] = field(default_factory=set)
    metadata: dict = field(default_factory=dict)


class HealthMonitor:
    def __init__(self, node_name: str = "health-monitor"):
        self.node_name = node_name
        self.node = ZreNode(node_name)
        self.node.set_header("X-ROLE", "health-monitor")
        self.peers: dict[str, PeerHealth] = {}
        self.status_callbacks: list = []
        self.all_groups: set[bytes] = {b"ALL", b"HEALTH"}

    async def start(
        self, port: int | None = None, interface: str | None = None, verbose: bool = False
    ):
        if port:
            self.node.set_port(port)
        if interface:
            self.node.set_interface(interface)
        if verbose:
            self.node.set_verbose(True)
        await self.node.start()
        for g in self.all_groups:
            await self.node.join(g)

    def register_status_callback(self, callback):
        self.status_callbacks.append(callback)

    def _notify(self, peer: PeerHealth, old: HealthStatus):
        for cb in self.status_callbacks:
            try:
                cb(peer, old)
            except Exception as exc:
                print(f"Callback error: {exc}")

    async def handle_event(self, event):
        t = event["type"]
        peer_id = event.get("peer_id", "")
        peer_name = event.get("peer_name", "unknown")
        if t == "ENTER":
            if peer_id not in self.peers:
                self.peers[peer_id] = PeerHealth(peer_id=peer_id, name=peer_name)
            else:
                self.peers[peer_id].name = peer_name
            self.peers[peer_id].status = HealthStatus.HEALTHY
            self.peers[peer_id].last_seen = time.time()
            print(f"[Monitor] Peer joined: {peer_name} ({peer_id[:8]})")
        elif t == "EXIT":
            if peer_id in self.peers:
                old = self.peers[peer_id].status
                self.peers[peer_id].status = HealthStatus.DEAD
                self._notify(self.peers[peer_id], old)
                print(f"[Monitor] Peer left: {peer_name} ({peer_id[:8]})")
        elif t == "EVASIVE":
            if peer_id in self.peers:
                old = self.peers[peer_id].status
                self.peers[peer_id].status = HealthStatus.EVASIVE
                self.peers[peer_id].evasive_count += 1
                self._notify(self.peers[peer_id], old)
                print(
                    f"[Monitor] Peer evasive: {peer_name} ({peer_id[:8]}) count:{self.peers[peer_id].evasive_count}"
                )
        elif t == "JOIN":
            group = event.get("group", "")
            if peer_id in self.peers:
                self.peers[peer_id].groups.add(group)
                print(f"[Monitor] {peer_name} joined group: {group}")
        elif t == "LEAVE":
            group = event.get("group", "")
            if peer_id in self.peers:
                self.peers[peer_id].groups.discard(group)
                print(f"[Monitor] {peer_name} left group: {group}")

    def get_cluster_health(self) -> dict:
        total = len(self.peers)
        healthy = sum(1 for p in self.peers.values() if p.status == HealthStatus.HEALTHY)
        evasive = sum(1 for p in self.peers.values() if p.status == HealthStatus.EVASIVE)
        dead = sum(1 for p in self.peers.values() if p.status == HealthStatus.DEAD)
        return {
            "total_peers": total,
            "healthy": healthy,
            "evasive": evasive,
            "dead": dead,
            "health_percentage": (healthy / total * 100) if total > 0 else 100,
        }

    async def run(
        self, port: int | None = None, interface: str | None = None, verbose: bool = False
    ):
        await self.start(port, interface, verbose)
        print(f"[{self.node_name}] Health monitor started (port {port})")

        async def ev_loop():
            async for event in self.node.events():
                await self.handle_event(event)

        try:
            await asyncio.gather(self.node.run(), ev_loop())
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            await self.node.stop()


async def status_callback(peer: PeerHealth, old: HealthStatus):
    if peer.status == HealthStatus.DEAD:
        print(f"  ALERT: Peer {peer.name} ({peer.peer_id[:8]}) went offline!")
    elif peer.status == HealthStatus.EVASIVE:
        print(f"  WARNING: Peer {peer.name} is evasive!")


def main():
    p = argparse.ArgumentParser(description="ZRE health monitor")
    p.add_argument("node_name", help="monitor node name")
    p.add_argument("--port", type=int, default=5670)
    p.add_argument("--interface", type=str, default=None)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    mon = HealthMonitor(args.node_name)
    mon.register_status_callback(lambda peer, old: asyncio.create_task(status_callback(peer, old)))
    try:
        asyncio.run(mon.run(args.port, args.interface, args.verbose))
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
