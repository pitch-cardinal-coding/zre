#!/usr/bin/env python3
"""
Fast tick demo — two nodes discovering each other at a 10ms beacon tick.

Proven host-to-bridge-VM: ENTER in well under a second with single-digit
CPU on each end. Pin --interface on multi-homed hosts so beacons egress
the subnet both ends share; otherwise they leave via the default route
only and the far end stays deaf.

Usage:
  fast_tick.py --role registry --port 14056 --interface virbr0
  fast_tick.py --role service --port 14056 --interface enp0s2
  (single box: drop --interface on both ends)
"""

import argparse
import asyncio
import time

from zre import ZreNode


async def main() -> None:
    parser = argparse.ArgumentParser(description="10ms beacon tick demo")
    parser.add_argument("--role", choices=("registry", "service"), required=True)
    parser.add_argument("--port", type=int, default=14056)
    parser.add_argument("--interface", type=str, default=None)
    parser.add_argument("--interval-ms", type=int, default=10)
    parser.add_argument("--run-seconds", type=int, default=30)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    node = ZreNode("tick-" + args.role)
    node.set_port(args.port)
    if args.interface:
        node.set_interface(args.interface)
    node.set_interval(args.interval_ms)
    if args.verbose:
        node.set_verbose(True)
    if args.role == "service":
        node.set_header("X-SERVICE", "tick")
    started = time.monotonic()
    await node.start()

    async def watch() -> None:
        async for event in node.events():
            if isinstance(event, dict) and event.get("type") == "ENTER":
                peer = str(event.get("peer_id", ""))[:8]
                print(
                    f"ENTER peer={peer} t={time.monotonic() - started:.2f}s", flush=True
                )

    run_task = asyncio.create_task(node.run())
    watch_task = asyncio.create_task(watch())
    await asyncio.sleep(0.5)
    if args.role == "service":
        await node.join("TICK")
    await asyncio.sleep(args.run_seconds)
    print(f"ROLE={args.role} peers={node.peers()}", flush=True)
    watch_task.cancel()
    run_task.cancel()
    await node.stop()


if __name__ == "__main__":
    asyncio.run(main())
