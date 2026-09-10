#!/usr/bin/env python3
"""
Service Discovery — Registry Pattern

Services announce themselves with metadata headers.
Other nodes can discover services by listening for ENTER events.

Usage:
  python service_discovery.py registry [--port PORT] [--interface IFACE] [--interval-ms MS]
  python service_discovery.py service <name> <type> <port> [--port PORT] [--interface IFACE] [--interval-ms MS]
  python service_discovery.py client [--port PORT] [--interface IFACE] [--interval-ms MS]

On multi-homed hosts pin --interface to the subnet both ends share;
otherwise beacons leave via the default route only.
"""

import argparse
import asyncio

from zre import ZreNode


async def run_service_registry(
    port: int, interface: str | None, interval_ms: int, verbose: bool
):
    node = ZreNode("service-registry")
    node.set_header("X-SERVICE", "registry")
    node.set_header("X-VERSION", "1.0")
    if port:
        node.set_port(port)
    if interface:
        node.set_interface(interface)
    node.set_interval(interval_ms)
    if verbose:
        node.set_verbose(True)
    await node.start()
    await node.join(b"SERVICES")

    print("Service Registry started. Discovering services...\n")
    services: dict[str, dict] = {}

    async def printer():
        async for event in node.events():
            t = event["type"]
            peer_id = event.get("peer_id", "")
            peer_name = event.get("peer_name", "")
            if t == "ENTER":
                print(f"  DISCOVERED: {peer_name} ({peer_id[:8]})")
                services[peer_id] = {"name": peer_name}
            elif t == "EXIT":
                if peer_id in services:
                    print(f"  LOST: {services[peer_id]['name']}")
                    del services[peer_id]
            elif t == "SHOUT" and event.get("group") == "SERVICES":
                payload = event.get("payload", b"")
                print(
                    f"  ANNOUNCEMENT from {peer_name}: {payload.decode(errors='replace')}"
                )

    try:
        await asyncio.gather(node.run(), printer())

    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await node.stop()


async def run_service(
    service_name: str,
    service_type: str,
    svc_port: int,
    port: int,
    interface: str | None,
    interval_ms: int,
    verbose: bool,
):
    node = ZreNode(service_name)
    node.set_header("X-SERVICE", service_type)
    node.set_header("X-PORT", str(svc_port))
    if port:
        node.set_port(port)
    if interface:
        node.set_interface(interface)
    node.set_interval(interval_ms)
    # Advertise where this box is actually reachable. The HELLO endpoint
    # carries the authoritative dial-back address; this header mirrors it
    # for human readers of the announcement.
    node.set_header("X-HOST", node._resolve_interface_ip() or "localhost")
    if verbose:
        node.set_verbose(True)
    await node.start()
    await node.join(b"SERVICES")
    announcement = f"{service_name}:{service_type}:{svc_port}".encode()
    await node.shout(b"SERVICES", announcement)

    print(
        f"[{service_name}] Service started on port {svc_port} — registered as {service_type}"
    )

    async def announce():
        # Repeat: a single startup shout races peer discovery and lands
        # nowhere when nobody has joined yet.
        while True:
            await asyncio.sleep(5)
            await node.shout(b"SERVICES", announcement)

    try:
        await asyncio.gather(node.run(), announce())
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await node.stop()


async def run_client(port: int, interface: str | None, interval_ms: int, verbose: bool):
    node = ZreNode("service-client")
    if port:
        node.set_port(port)
    if interface:
        node.set_interface(interface)
    node.set_interval(interval_ms)
    if verbose:
        node.set_verbose(True)
    await node.start()
    await node.join(b"SERVICES")

    print("Client: Discovering services...\n")

    async def printer():
        async for event in node.events():
            t = event["type"]
            if t == "ENTER":
                peer_name = event.get("peer_name", "")
                print(f"  Found service: {peer_name}")
            elif t == "SHOUT" and event.get("group") == "SERVICES":
                payload = event.get("payload", b"")
                parts = payload.decode(errors="replace").split(":")
                if len(parts) >= 3:
                    svc_name, svc_type, svc_port = parts[0], parts[1], parts[2]
                    print(f"  Service: {svc_name} ({svc_type}) on port {svc_port}")

    try:
        await asyncio.gather(node.run(), printer())

    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await node.stop()


def main():
    p = argparse.ArgumentParser(description="ZRE service discovery")
    sub = p.add_subparsers(dest="mode", required=True)

    pr = sub.add_parser("registry", help="run registry")
    pr.add_argument("--port", type=int, default=15670)
    pr.add_argument("--interface", type=str, default=None)
    pr.add_argument("--interval-ms", type=int, default=1000)
    pr.add_argument("--verbose", action="store_true")

    ps = sub.add_parser("service", help="run a service")
    ps.add_argument("name", help="service name")
    ps.add_argument("type", help="service type")
    ps.add_argument("svc_port", type=int, help="service port")
    ps.add_argument("--port", type=int, default=15670)
    ps.add_argument("--interface", type=str, default=None)
    ps.add_argument("--interval-ms", type=int, default=1000)
    ps.add_argument("--verbose", action="store_true")

    pc = sub.add_parser("client", help="run client")
    pc.add_argument("--port", type=int, default=15670)
    pc.add_argument("--interface", type=str, default=None)
    pc.add_argument("--interval-ms", type=int, default=1000)
    pc.add_argument("--verbose", action="store_true")

    args = p.parse_args()
    if args.mode == "registry":
        try:
            asyncio.run(
                run_service_registry(
                    args.port, args.interface, args.interval_ms, args.verbose
                )
            )
        except KeyboardInterrupt:
            print("\nInterrupted, shutting down...")
    elif args.mode == "service":
        try:
            asyncio.run(
                run_service(
                    args.name,
                    args.type,
                    args.svc_port,
                    args.port,
                    args.interface,
                    args.interval_ms,
                    args.verbose,
                )
            )
        except KeyboardInterrupt:
            print("\nInterrupted, shutting down...")
    elif args.mode == "client":
        try:
            asyncio.run(
                run_client(args.port, args.interface, args.interval_ms, args.verbose)
            )
        except KeyboardInterrupt:
            print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
