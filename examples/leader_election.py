#!/usr/bin/env python3
"""Group leader election — exactly one coordinator wins per group.

Every node contests the same group; the mesh holds a bully election
(lowest peer id wins) and each node prints a LEADER event naming the
winner. Kill the winner and the survivors re-elect within seconds.

Run two or more nodes (any mix of hosts), same group:

    python3 examples/leader_election.py alice --group WORKERS --port 15670
    python3 examples/leader_election.py bob --group WORKERS --port 15670

Give stable ids to make the winner predictable:

    python3 examples/leader_election.py alice --uuid alice-lab --group WORKERS
"""

import argparse
import asyncio
import sys
import uuid

from _common import (
    CollisionExit,
    add_uuid_arg,
    check_collision_event,
    exit_on_uuid_collision,
)

from zre import UUIDCollisionError, ZreNode


async def run_node(args):
    node = ZreNode(args.name)
    if args.uuid:
        node.set_uuid(args.uuid)
    if args.port:
        node.set_port(args.port)
    if args.interface:
        node.set_interface(args.interface)
    if args.verbose:
        node.set_verbose(True)
    node.set_contest_in_group(args.group)
    await node.start()
    print(
        f"[{args.name}] READY uuid={node.peer_id_hex[:8]} contesting {args.group}",
        flush=True,
    )

    run_task = asyncio.create_task(node.run())
    await node.join(args.group.encode())

    async def events():
        async for event in node.events():
            check_collision_event(event)
            etype = event["type"]
            if etype == "LEADER":
                mine = " (me!)" if event.get("peer_id") == node.peer_id_hex else ""
                print(
                    f"[{args.name}] LEADER {event.get('peer_name')} "
                    f"{event.get('peer_id', '')[:8]} in {event.get('group')}{mine}",
                    flush=True,
                )
            elif etype in ("ENTER", "EXIT", "JOIN", "LEAVE", "EVASIVE"):
                print(
                    f"[{args.name}] {etype} {event.get('peer_name', '?')}",
                    flush=True,
                )

    try:
        if args.wait and args.wait > 0:
            await asyncio.wait_for(events(), timeout=args.wait)
        else:
            await events()
    except asyncio.TimeoutError:
        pass
    finally:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        await node.stop()
        print(f"[{args.name}] stopped", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="ZRE leader-election example")
    parser.add_argument(
        "name", nargs="?", default=f"voter-{uuid.uuid4().hex[:4]}", help="node name"
    )
    parser.add_argument("--port", type=int, default=15670, help="beacon UDP port")
    parser.add_argument("--interface", default=None, help="network interface or IP")
    add_uuid_arg(parser)
    parser.add_argument(
        "--group", default="WORKERS", help="group to contest leadership of"
    )
    parser.add_argument(
        "--wait", type=float, default=0, help="seconds to run (0 = until Ctrl-C)"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        asyncio.run(run_node(args))
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
    except CollisionExit:
        sys.exit(3)
    except UUIDCollisionError as exc:
        sys.exit(exit_on_uuid_collision(exc))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
    except CollisionExit:
        sys.exit(3)
    except UUIDCollisionError as exc:
        sys.exit(exit_on_uuid_collision(exc))
