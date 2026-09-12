#!/usr/bin/env python3
"""Direct (WAN) peer connection — dial by address, no LAN beacons needed.

Two roles, same script. Beacons still run locally but discovery
goes through an explicit address, so peers find each other across
subnets where UDP broadcast cannot travel.

Stable side (fixed TCP port, e.g. on the far host):

    python3 examples/wan_direct.py remote --listen-port 19870 --port 19871

Client side (this host):

    python3 examples/wan_direct.py local --peer 192.168.122.87:19870 \\
        --send "Hello WAN!" --wait 20

Options mirror the LAN examples plus:
  --peer HOST:PORT        direct-connect target (repeatable)
  --listen-port PORT      pin the ROUTER socket to a fixed TCP port
  --advertised-endpoint   public tcp://host:port told to peers in HELLO
                          (NAT/port-forward setups: advertise the public address)
"""

import argparse
import asyncio
import sys
import uuid

from zre import ZreNode


def parse_peer(value: str):
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError(
            f"peer must look like HOST:PORT, got {value!r}"
        )
    return host, int(port)


async def run_node(args):
    node = ZreNode(args.name)
    if args.port:
        node.set_port(args.port)
    if args.interface:
        node.set_interface(args.interface)
    if args.listen_port:
        node.set_beacon_peer_port(args.listen_port)
    if args.advertised_endpoint:
        node.set_advertised_endpoint(args.advertised_endpoint)
    if args.verbose:
        node.set_verbose(True)
    await node.start()
    print(
        f"[{args.name}] READY inbox={node._inbox_port} "
        f"beacon={node._beacon_port} uuid={node.peer_id_hex[:8]}",
        flush=True,
    )

    run_task = asyncio.create_task(node.run())
    await asyncio.sleep(0.5)

    for host, port in args.peer:
        await node.connect_peer(host, port)
        print(f"[{args.name}] connecting to {host}:{port}", flush=True)

    await node.join(args.group.encode())

    sent = False

    async def events():
        nonlocal sent
        async for event in node.events():
            etype = event["type"]
            peer = event.get("peer_name", "?")
            if etype == "ENTER":
                print(
                    f"[{args.name}] ENTER {peer} {event.get('address', '')}", flush=True
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
    parser = argparse.ArgumentParser(description="ZRE direct-connect example")
    parser.add_argument(
        "name", nargs="?", default=f"wan-{uuid.uuid4().hex[:4]}", help="node name"
    )
    parser.add_argument("--port", type=int, default=15670, help="beacon UDP port")
    parser.add_argument("--interface", default=None, help="network interface or IP")
    parser.add_argument(
        "--listen-port", type=int, default=None, help="fixed TCP ROUTER port"
    )
    parser.add_argument(
        "--peer",
        action="append",
        type=parse_peer,
        default=[],
        metavar="HOST:PORT",
        help="direct-connect target (repeatable)",
    )
    parser.add_argument(
        "--advertised-endpoint", default=None, help="public tcp://host:port in HELLO"
    )
    parser.add_argument("--group", default="CHAT", help="group to join")
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


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
        sys.exit(0)
