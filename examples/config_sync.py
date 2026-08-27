#!/usr/bin/env python3
"""
Distributed Configuration Sync — Configuration propagation across nodes

Nodes can publish configuration updates that propagate to all peers.

Usage:
  python config_sync.py <node_name> [--port PORT]
"""

import argparse
import asyncio
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

from zre import ZreNode

logger = logging.getLogger(__name__)


@dataclass
class ConfigUpdate:
    key: str
    value: Any
    version: int
    timestamp: float
    source_id: str
    checksum: str


class ConfigManager:
    """Distributed configuration manager with last-writer-wins."""

    def __init__(self, node_name: str):
        self.node_name = node_name
        self.node_id = f"config-{node_name}"
        self.node = ZreNode(self.node_id)
        self.node.set_header("X-ROLE", "config-manager")
        self.config_group = b"CONFIG"
        self.config: dict[str, Any] = {}
        self.versions: dict[str, int] = {}
        self.watchers: dict[str, list] = {}

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
        await self.node.join(self.config_group)

    def _checksum(self, value: Any) -> str:
        data = json.dumps(value, sort_keys=True).encode()
        return hashlib.sha256(data).hexdigest()[:16]

    def set(self, key: str, value: Any):
        version = self.versions.get(key, 0) + 1
        self.versions[key] = version
        update = ConfigUpdate(
            key=key,
            value=value,
            version=version,
            timestamp=time.time(),
            source_id=self.node_id,
            checksum=self._checksum(value),
        )
        self.config[key] = value
        for cb in self.watchers.get(key, []):
            try:
                cb(key, value)
            except Exception as exc:
                logger.error("Watcher error: %s", exc)
        payload = json.dumps(asdict(update)).encode()
        _task = asyncio.create_task(self.node.shout(self.config_group, payload))
        # keep reference for RUF006
        _ = _task

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def watch(self, key: str, callback):
        self.watchers.setdefault(key, []).append(callback)

    async def handle_message(self, event):
        if event.get("type") != "SHOUT" or event.get("group") != "CONFIG":
            return
        try:
            payload = json.loads(event["payload"].decode())
            update = ConfigUpdate(**payload)
            if update.source_id == self.node_id:
                return
            current_version = self.versions.get(update.key, 0)
            if update.version <= current_version:
                return
            if self._checksum(update.value) != update.checksum:
                print(f"[{self.node_name}] Checksum mismatch for {update.key}")
                return
            self.config[update.key] = update.value
            self.versions[update.key] = update.version
            for cb in self.watchers.get(update.key, []):
                try:
                    cb(update.key, update.value)
                except Exception as exc:
                    logger.error("Watcher error: %s", exc)
            print(
                f"[{self.node_name}] Config updated: {update.key} = {update.value} (v{update.version})"
            )
        except Exception as exc:
            logger.error("Config sync error: %s", exc)

    async def run(
        self, port: int | None = None, interface: str | None = None, verbose: bool = False
    ):
        await self.start(port, interface, verbose)
        print(f"[{self.node_name}] Config manager started (port {port})")

        async def event_loop():
            async for event in self.node.events():
                await self.handle_message(event)

        try:
            await asyncio.gather(self.node.run(), event_loop())
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            await self.node.stop()


def main():
    p = argparse.ArgumentParser(description="ZRE config sync")
    p.add_argument("node_name", help="node name")
    p.add_argument("--port", type=int, default=5670)
    p.add_argument("--interface", type=str, default=None)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    mgr = ConfigManager(args.node_name)
    mgr.watch("database_url", lambda k, v: print(f"  DB URL changed: {v}"))
    mgr.watch("feature_flags", lambda k, v: print(f"  Feature flags: {v}"))

    # Demo with preset sets
    async def full():
        await mgr.start(args.port, args.interface, args.verbose)
        # background run
        rt = asyncio.create_task(mgr.node.run())

        # event printer
        async def ev_loop():
            async for evt in mgr.node.events():
                await mgr.handle_message(evt)

        et = asyncio.create_task(ev_loop())
        if args.node_name == "node-1":
            await asyncio.sleep(2)
            mgr.set("database_url", "postgresql://localhost:5432/mydb")
            await asyncio.sleep(1)
            mgr.set("feature_flags", {"new_ui": True, "beta": False})
            await asyncio.sleep(1)
            mgr.set("cache_ttl", 300)
        await asyncio.sleep(10)
        rt.cancel()
        et.cancel()
        await asyncio.gather(rt, et, return_exceptions=True)
        await mgr.node.stop()

    try:
        asyncio.run(full())
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
