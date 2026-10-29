#!/usr/bin/env python3
"""
ccalls app — Python side of the C-sensors demo.

Joins CHAT, prints every sensor reading it hears, sends a `cmd-refresh`
command to the group every --every seconds, and greets each newcomer with
a WHISPER. Run it together with two `sensor` C peers (see HOW-TO-RUN.md):

  python3 examples/ccalls/app.py --port 5670
"""

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _common import (
    CollisionExit,
    add_uuid_arg,
    check_collision_event,
    exit_on_uuid_collision,
)

from zre import UUIDCollisionError, ZreNode


async def run_app(
    name: str,
    port: int,
    group: str,
    every: float,
    duration: float,
    interface: str | None,
    verbose: bool,
    uuid_hex: str | None = None,
):
    node = ZreNode(name)
    if uuid_hex:
        node.set_uuid(uuid_hex)
    node.set_header("X-ROLE", "app")
    if port:
        node.set_port(port)
    if interface:
        node.set_interface(interface)
    if verbose:
        node.set_verbose(True)
    await node.start()
    await node.join(group)
    run_task = asyncio.create_task(node.run())
    print(f"[app {name}] up, group {group}, id {node.peer_id_hex}", flush=True)

    started = time.monotonic()
    last_cmd = started
    counter = 0

    try:
        while time.monotonic() - started < duration:
            event = await node.recv(timeout=0.5)
            if event is not None:
                check_collision_event(event)
                etype = event["type"]
                if etype == "ENTER":
                    print(f"[app] peer {event['peer_name']} entered", flush=True)
                    await node.whispers(event["peer_id"], "hi-from-app")
                elif etype == "SHOUT" and event.get("group") == group:
                    payload = event.get("payload", b"")
                    print(
                        f"[app] reading from {event['peer_name']}: "
                        f"{payload.decode(errors='replace')}",
                        flush=True,
                    )
                elif etype == "WHISPER":
                    print(
                        f"[app] private from {event['peer_name']}: "
                        f"{event.get('payload', b'').decode(errors='replace')}",
                        flush=True,
                    )
                elif etype == "EXIT":
                    print(f"[app] peer {event['peer_name']} left", flush=True)
            if time.monotonic() - last_cmd >= every:
                counter += 1
                await node.shouts(group, f"cmd-refresh #{counter}")
                print(f"[app] sent cmd-refresh #{counter}", flush=True)
                last_cmd = time.monotonic()
    finally:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        await node.stop()


def main():
    parser = argparse.ArgumentParser(description="ccalls Python app")
    parser.add_argument("--name", default="pyapp")
    parser.add_argument("--port", type=int, default=5670)
    parser.add_argument("--group", default="CHAT")
    parser.add_argument("--every", type=float, default=4.0)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--interface", default=None)
    add_uuid_arg(parser)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(
            run_app(
                args.name,
                args.port,
                args.group,
                args.every,
                args.duration,
                args.interface,
                args.verbose,
                getattr(args, "uuid", None),
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
    except CollisionExit:
        sys.exit(3)
    except UUIDCollisionError as exc:
        sys.exit(exit_on_uuid_collision(exc))


if __name__ == "__main__":
    main()
