#!/usr/bin/env python3
"""
Presence — Lightweight who-is-online tracker.

Prints ENTER/EXIT/EVASIVE and maintains a live peer table.
Simplest example for Local Network / IoT / Event venues.

Usage:
  python3 examples/presence.py alice --port 15670
  python3 examples/presence.py bob --port 15670 --verbose
"""

import argparse
import asyncio
import time

from zre import ZreNode


async def run_presence(name: str, port: int, interface: str | None, verbose: bool):
    node = ZreNode(name)
    node.set_header("X-ROLE", "presence")
    if port:
        node.set_port(port)
    if interface:
        node.set_interface(interface)
    if verbose:
        node.set_verbose(True)
    await node.start()
    run_task = asyncio.create_task(node.run())
    # presence is beacon-only, but still wait for discovery to avoid seq race
    for _ in range(15):
        if node.peers():
            break
        await asyncio.sleep(0.2)
    # presence needs no group, just beacons; join ALL for demo
    await node.join(b"ALL")
    print(f"[presence {name}] started — beacon {port}, id {node.peer_id_hex[:8]}")
    print(f"[presence {name}] waiting for peers... (Ctrl-C to leave)")

    peers: dict[str, dict] = {}
    start = time.time()

    async def handle_events():
        async for event in node.events():
            etype = event["type"]
            pid = event.get("peer_id", "")[:8]
            pname = event.get("peer_name", "?")
            if etype == "ENTER":
                peers[event["peer_id"]] = {"name": pname, "seen": time.time()}
                print(f"  + ENTER {pname} ({pid}) now {len(peers)} peers")
            elif etype == "EXIT":
                peers.pop(event["peer_id"], None)
                print(f"  - EXIT  {pname} ({pid}) now {len(peers)} peers")
            elif etype == "EVASIVE":
                print(f"  ! EVASIVE {pname} ({pid})")
            elif etype == "JOIN":
                print(f"    JOIN {pname} -> {event.get('group')}")
            elif etype == "LEAVE":
                print(f"    LEAVE {pname} -> {event.get('group')}")

    async def status_loop():
        while True:
            await asyncio.sleep(5)
            elapsed = int(time.time() - start)
            print(
                f"[presence {name}] {elapsed}s — {len(peers)} peers, own {node.own_groups()}, peers={list(node.peers())[:3]}"
            )

    try:
        await asyncio.gather(handle_events(), status_loop())

    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        await node.stop()
        print(f"[presence {name}] stopped")


def main():
    parser = argparse.ArgumentParser(description="ZRE presence")
    parser.add_argument("name", help="peer name")
    parser.add_argument("--port", type=int, default=15670)
    parser.add_argument("--interface", type=str, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run_presence(args.name, args.port, args.interface, args.verbose))
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
