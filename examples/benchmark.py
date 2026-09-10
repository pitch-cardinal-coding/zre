#!/usr/bin/env python3
"""
Performance Benchmark — Measure ZRE throughput and latency

Usage:
  python benchmark.py discovery [--nodes N] [--port PORT] [--interface IFACE]
  python benchmark.py throughput [--msgs N] [--size S] [--port PORT] [--interface IFACE]
  python benchmark.py latency [--pings N] [--port PORT] [--interface IFACE]
  python benchmark.py scalability [--port PORT] [--interface IFACE]
"""

import argparse
import asyncio
import statistics
import time

from zre import ZreNode


async def benchmark_discovery(
    num_nodes: int = 10,
    port: int = 5670,
    verbose: bool = False,
    interface: str | None = None,
):
    """Benchmark peer discovery time."""
    print(f"\n=== Discovery Benchmark: {num_nodes} nodes (port {port}) ===")

    nodes = [ZreNode(f"bench-{i}") for i in range(num_nodes)]
    for n in nodes:
        n.set_port(port)
        if interface:
            n.set_interface(interface)
        if verbose:
            n.set_verbose(True)
        await n.start()

    tasks = [asyncio.create_task(n.run()) for n in nodes]

    start = time.time()
    target_peers = num_nodes - 1
    try:
        while True:
            all_discovered = all(len(n.peers()) >= target_peers for n in nodes)
            if all_discovered:
                break
            await asyncio.sleep(0.1)
            if time.time() - start > 30:
                print("Timeout!")
                break

        elapsed = time.time() - start
        print(f"All {num_nodes} nodes discovered each other in {elapsed:.2f}s")
        return elapsed
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for n in nodes:
            await n.stop()


async def benchmark_throughput(
    num_messages: int = 1000,
    payload_size: int = 1024,
    port: int = 5670,
    interface: str | None = None,
):
    """Benchmark message throughput."""
    print(
        f"\n=== Throughput Benchmark: {num_messages} msgs x {payload_size} bytes (port {port}) ==="
    )

    node1 = ZreNode("sender")
    node2 = ZreNode("receiver")
    node1.set_port(port)
    node2.set_port(port)
    if interface:
        node1.set_interface(interface)
        node2.set_interface(interface)
    await node1.start()
    await node2.start()

    t1 = asyncio.create_task(node1.run())
    t2 = asyncio.create_task(node2.run())

    await asyncio.sleep(2)

    await node1.join(b"BENCH")
    await node2.join(b"BENCH")
    await asyncio.sleep(1)

    payload = b"x" * payload_size
    received = 0

    async def receiver():
        nonlocal received
        async for event in node2.events():
            if event["type"] == "SHOUT":
                received += 1
                if received >= num_messages:
                    break

    recv_task = asyncio.create_task(receiver())

    try:
        start = time.time()
        for i in range(num_messages):
            await node1.shout(b"BENCH", payload)
            if i % 500 == 0:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(recv_task, timeout=10)
        elapsed = time.time() - start

        throughput = num_messages / elapsed if elapsed > 0 else 0
        if elapsed > 0:
            mbps = (num_messages * payload_size * 8) / elapsed / 1_000_000
        else:
            mbps = 0.0

        print(f"Sent {num_messages} messages in {elapsed:.2f}s")
        print(f"Throughput: {throughput:.0f} msg/s, {mbps:.1f} Mbps")
        return throughput
    except asyncio.TimeoutError:
        print(f"Timeout: only {received}/{num_messages} received")
        recv_task.cancel()
        return 0
    finally:
        t1.cancel()
        t2.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)
        await node1.stop()
        await node2.stop()


async def benchmark_latency(
    num_pings: int = 100,
    port: int = 5670,
    interface: str | None = None,
):
    """Benchmark message latency (whisper round-trip)."""
    print(f"\n=== Latency Benchmark: {num_pings} pings (port {port}) ===")

    node1 = ZreNode("ping-sender")
    node2 = ZreNode("ping-receiver")
    node1.set_port(port)
    node2.set_port(port)
    if interface:
        node1.set_interface(interface)
        node2.set_interface(interface)
    await node1.start()
    await node2.start()

    t1 = asyncio.create_task(node1.run())
    t2 = asyncio.create_task(node2.run())

    await asyncio.sleep(2)

    latencies: list[float] = []
    pong_event = asyncio.Event()

    async def receiver2():
        async for event in node2.events():
            if event["type"] == "WHISPER":
                await node2.whisper(event["peer_id"], b"PONG")

    async def receiver1():
        async for event in node1.events():
            if event["type"] == "WHISPER" and event.get("payload") == b"PONG":
                pong_event.set()

    r2 = asyncio.create_task(receiver2())
    r1 = asyncio.create_task(receiver1())

    # wait for discovery
    start_wait = time.time()
    while time.time() - start_wait < 5 and not node1.peers():
        await asyncio.sleep(0.1)
    if not node1.peers():
        print("No peers discovered, latency test aborted")
        r1.cancel()
        r2.cancel()
        t1.cancel()
        t2.cancel()
        await asyncio.gather(r1, r2, t1, t2, return_exceptions=True)
        await node1.stop()
        await node2.stop()
        return

    peers = node1.peers()
    try:
        for _ in range(num_pings):
            start = time.perf_counter()
            pong_event.clear()
            await node1.whisper(peers[0], b"PING")
            try:
                await asyncio.wait_for(pong_event.wait(), timeout=1.0)
                latency = (time.perf_counter() - start) * 1000
                latencies.append(latency)
            except asyncio.TimeoutError:
                latencies.append(float("inf"))
            await asyncio.sleep(0.01)

        valid = [v for v in latencies if v != float("inf")]
        if valid:
            p99 = (
                statistics.quantiles(valid, n=100)[98]
                if len(valid) >= 100
                else max(valid)
            )
            print(
                f"Latency (ms): min={min(valid):.2f}, max={max(valid):.2f}, mean={statistics.mean(valid):.2f}, p50={statistics.median(valid):.2f}, p99={p99:.2f}"
            )
            print(f"Lost: {len(latencies) - len(valid)}/{len(latencies)}")
        else:
            print("No valid replies")
    finally:
        r1.cancel()
        r2.cancel()
        t1.cancel()
        t2.cancel()
        await asyncio.gather(r1, r2, t1, t2, return_exceptions=True)
        await node1.stop()
        await node2.stop()


async def benchmark_scalability(port: int = 5670, interface: str | None = None):
    """Test scalability with increasing node counts."""
    print("\n=== Scalability Benchmark ===")
    for num_nodes in [5, 10, 15, 20]:
        print(f"\n--- Testing {num_nodes} nodes ---")
        try:
            elapsed = await benchmark_discovery(
                num_nodes, port=port, interface=interface
            )
            print(f"  Discovery time: {elapsed:.2f}s")
        except Exception as exc:
            print(f"  Failed: {exc}")
            break


def main():
    p = argparse.ArgumentParser(description="ZRE benchmark")
    p.add_argument(
        "benchmark", choices=["discovery", "throughput", "latency", "scalability"]
    )
    p.add_argument("--nodes", type=int, default=10, help="num nodes for discovery")
    p.add_argument("--msgs", type=int, default=1000, help="num msgs for throughput")
    p.add_argument("--size", type=int, default=1024, help="payload size")
    p.add_argument("--pings", type=int, default=100, help="num pings for latency")
    p.add_argument("--port", type=int, default=5670, help="beacon port")
    p.add_argument(
        "--interface", type=str, default=None, help="pin beacons to this interface"
    )
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    try:
        if args.benchmark == "discovery":
            asyncio.run(
                benchmark_discovery(
                    args.nodes,
                    port=args.port,
                    verbose=args.verbose,
                    interface=args.interface,
                )
            )
        elif args.benchmark == "throughput":
            asyncio.run(
                benchmark_throughput(
                    args.msgs, args.size, port=args.port, interface=args.interface
                )
            )
        elif args.benchmark == "latency":
            asyncio.run(
                benchmark_latency(args.pings, port=args.port, interface=args.interface)
            )
        elif args.benchmark == "scalability":
            asyncio.run(benchmark_scalability(port=args.port, interface=args.interface))
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
