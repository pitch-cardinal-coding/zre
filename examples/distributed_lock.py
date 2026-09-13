#!/usr/bin/env python3
"""
Distributed Lock — Mutual exclusion across peers

Usage:
  python distributed_lock.py <node_name> [--port PORT]
"""

import argparse
import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import dataclass
from enum import Enum

from zre import ZreNode


class LockState(Enum):
    FREE = "free"
    LOCKED = "locked"
    REQUESTED = "requested"


@dataclass
class LockRequest:
    lock_name: str
    requester_id: str
    request_id: str
    timestamp: float


class DistributedLock:
    def __init__(self, node_name: str):
        self.node_name = node_name
        self.node_id = f"lock-{node_name}"
        self.node = ZreNode(self.node_id)
        self.node.set_header("X-ROLE", "lock-manager")
        self.lock_group = b"LOCKS"
        self.locks: dict[str, LockState] = {}
        self.pending: dict[str, list[LockRequest]] = {}
        self.held: set[str] = set()
        self.votes: dict[str, set[str]] = {}
        self._awaiting_grant: tuple[str, str] | None = None

    async def start(
        self,
        port: int | None = None,
        interface: str | None = None,
        verbose: bool = False,
    ):
        if port:
            self.node.set_port(port)
        if interface:
            self.node.set_interface(interface)
        if verbose:
            self.node.set_verbose(True)
        await self.node.start()
        await self.node.join(self.lock_group)

    async def acquire(self, lock_name: str, timeout: float = 10.0) -> bool:
        request_id = uuid.uuid4().hex[:8]
        req = LockRequest(lock_name, self.node_id, request_id, time.time())
        self.pending.setdefault(lock_name, []).append(req)
        self.votes.setdefault(lock_name, set()).add(self.node_id)
        self._awaiting_grant = (lock_name, request_id)
        payload = {
            "type": "LOCK_REQUEST",
            "lock_name": lock_name,
            "requester_id": self.node_id,
            "request_id": request_id,
            "timestamp": req.timestamp,
        }
        await self.node.shout(self.lock_group, json.dumps(payload).encode())
        start = time.time()
        while time.time() - start < timeout:
            peers = self.node.peers()
            # majority including self
            needed = max(1, (len(peers) + 1) // 2 + 1)
            if len(self.votes.get(lock_name, set())) >= needed:
                self.held.add(lock_name)
                self.locks[lock_name] = LockState.LOCKED
                self._awaiting_grant = None
                return True
            await asyncio.sleep(0.1)
        self._awaiting_grant = None
        return False

    async def release(self, lock_name: str):
        if lock_name not in self.held:
            return
        self.held.discard(lock_name)
        self.locks[lock_name] = LockState.FREE
        payload = {
            "type": "LOCK_RELEASE",
            "lock_name": lock_name,
            "holder_id": self.node_id,
        }
        await self.node.shout(self.lock_group, json.dumps(payload).encode())

    async def handle_message(self, event):
        if event.get("type") == "WHISPER":
            try:
                payload = json.loads(event["payload"].decode())
            except ValueError:
                return
            if payload.get("type") == "LOCK_GRANT":
                awaiting = self._awaiting_grant
                if awaiting and payload.get("request_id") == awaiting[1]:
                    self.votes.setdefault(awaiting[0], set()).add(
                        payload.get("holder_id")
                    )
            return
        if event.get("type") != "SHOUT" or event.get("group") != "LOCKS":
            return
        try:
            payload = json.loads(event["payload"].decode())
            msg_type = payload.get("type")
            lock_name = payload.get("lock_name")
            if msg_type == "LOCK_REQUEST":
                requester = payload.get("requester_id")
                if self.locks.get(lock_name) != LockState.LOCKED:
                    self.votes.setdefault(lock_name, set()).add(requester)
                    grant = {
                        "type": "LOCK_GRANT",
                        "lock_name": lock_name,
                        "requester_id": requester,
                        "request_id": payload.get("request_id"),
                        "holder_id": self.node_id,
                    }
                    await self.node.whisper(requester, json.dumps(grant).encode())
            elif msg_type == "LOCK_RELEASE":
                if lock_name in self.locks:
                    self.locks[lock_name] = LockState.FREE
        except Exception as exc:
            print(f"Error handling lock message: {exc}")

    async def run(
        self,
        port: int | None = None,
        interface: str | None = None,
        verbose: bool = False,
    ):
        await self.start(port, interface, verbose)
        print(f"[{self.node_name}] Lock manager started (port {port})")

        async def ev_loop():
            async for event in self.node.events():
                await self.handle_message(event)

        try:
            await asyncio.gather(self.node.run(), ev_loop())
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            for lk in list(self.held):
                with asyncio.CancelledError, contextlib.suppress(Exception):
                    await self.release(lk)
            await self.node.stop()


def main():
    parser = argparse.ArgumentParser(description="ZRE distributed lock")
    parser.add_argument("node_name", help="node name")
    parser.add_argument("--port", type=int, default=15670)
    parser.add_argument("--interface", type=str, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    lock = DistributedLock(args.node_name)

    async def demo():
        run_task = asyncio.create_task(
            lock.run(args.port, args.interface, args.verbose)
        )
        await asyncio.sleep(2)
        for _ in range(25):
            if lock.node.peers():
                break
            await asyncio.sleep(0.2)
        success = await lock.acquire("resource-1", timeout=10.0)
        if success:
            print(f"[{args.node_name}] Got lock! Doing work...")
            await asyncio.sleep(3)
            await lock.release("resource-1")
            print(f"[{args.node_name}] Released lock")
        else:
            print(f"[{args.node_name}] Failed to acquire lock")
        await asyncio.sleep(1)
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
        await lock.node.stop()

    try:
        asyncio.run(demo())
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
