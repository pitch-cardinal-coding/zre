#!/usr/bin/env python3
"""UDP-free mesh via a gossip hub — discovery without LAN beacons.

Two roles, same script. Use where UDP broadcast cannot travel (cloud
VPCs, containers, WAN links): one well-known hub relays endpoint
announcements, and every node finds every other node through it.

Hub side (hosts the rendezvous point and joins the mesh itself):

    python3 examples/gossip_mesh.py hub --hub-port 15671 --port 15670

Join side (any number of nodes, anywhere that can reach the hub):

    python3 examples/gossip_mesh.py join --hub 192.168.122.1:15671 \\
        --send "Hello mesh!" --wait 20

A standalone hub (no mesh membership) is also available:

    python3 -m zre.gossip --port 15671
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
    if args.role == "hub":
        node.gossip_bind(f"tcp://0.0.0.0:{args.hub_port}")
        print(f"[{args.name}] hub on 0.0.0.0:{args.hub_port}", flush=True)
    else:
        node.gossip_connect(args.hub)
        print(f"[{args.name}] connecting to hub {args.hub}", flush=True)
    await node.start()
    print(
        f"[{args.name}] READY inbox={node._inbox_port} "
        f"uuid={node.peer_id_hex[:8]} (beacons off, gossip only)",
        flush=True,
    )

    run_task = asyncio.create_task(node.run())
    await node.join(args.group.encode())

    sent = False

    async def events():
        nonlocal sent
        async for event in node.events():
            check_collision_event(event)
            etype = event["type"]
            peer = event.get("peer_name", "?")
            if etype == "ENTER":
                print(
                    f"[{args.name}] ENTER {peer} {event.get('address', '')}",
                    flush=True,
                )
                if args.send and not sent:
                    await asyncio.sleep(1.0)
                    await node.shout(args.group.encode(), args.send.encode())
                    print(f"[{args.name}] SHOUT {args.send!r}", flush=True)
                    sent = True
            elif etype == "SHOUT":
                payload = event.get("payload", b"")
                if isinstance(payload, bytes):
                    payload = payload.decode("utf-8", errors="replace")
                print(f"[{args.name}] SHOUT from {peer}: {payload}", flush=True)
            elif etype == "WHISPER":
                payload = event.get("payload", b"")
                if isinstance(payload, bytes):
                    payload = payload.decode("utf-8", errors="replace")
                print(f"[{args.name}] WHISPER from {peer}: {payload}", flush=True)
            elif etype in ("JOIN", "LEAVE", "EXIT", "EVASIVE"):
                print(f"[{args.name}] {etype} {peer}", flush=True)

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
    parser = argparse.ArgumentParser(description="ZRE gossip-mesh example")
    parser.add_argument(
        "role", nargs="?", default="join", choices=("hub", "join"), help="node role"
    )
    parser.add_argument(
        "name", nargs="?", default=f"mesh-{uuid.uuid4().hex[:4]}", help="node name"
    )
    parser.add_argument("--port", type=int, default=15670, help="beacon UDP port")
    parser.add_argument("--interface", default=None, help="network interface or IP")
    parser.add_argument(
        "--hub-port", type=int, default=15671, help="hub TCP port (hub role)"
    )
    parser.add_argument(
        "--hub",
        default="127.0.0.1:15671",
        help="hub HOST:PORT to connect to (join role)",
    )
    add_uuid_arg(parser)
    parser.add_argument("--group", default="MESH", help="group to join")
    parser.add_argument("--send", default=None, help="message to SHOUT after ENTER")
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
