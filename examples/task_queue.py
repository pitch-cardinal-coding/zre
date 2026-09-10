#!/usr/bin/env python3
"""
Distributed Task Queue — Workers discover coordinator via ZRE.

Coordinator SHOUTs tasks to group TASKS; workers WHISPER results back.
Demonstrates Enterprise scenario: distributed task queues.

Usage:
  python3 examples/task_queue.py coordinator --port 5670
  python3 examples/task_queue.py worker worker-1 --port 5670
"""

import argparse
import asyncio
import json
import time
import uuid

from zre import ZreNode


async def run_coordinator(port: int, interface: str | None, verbose: bool):
    node = ZreNode("coordinator")
    node.set_header("X-ROLE", "coordinator")
    if port:
        node.set_port(port)
    if interface:
        node.set_interface(interface)
    if verbose:
        node.set_verbose(True)
    await node.start()
    run_task = asyncio.create_task(node.run())
    # wait for beacon discovery to avoid JOIN before HELLO
    for _ in range(30):
        if node.peers():
            break
        await asyncio.sleep(0.2)
    await node.join(b"TASKS")
    print(f"[coordinator] started on beacon {port}, waiting for workers...")
    # run_task already started, handle_events will be separate

    pending: dict[str, dict] = {}

    async def handle_events():
        async for event in node.events():
            t = event["type"]
            if t == "ENTER":
                print(f"[coordinator] worker {event.get('peer_name')} entered")
            elif t == "WHISPER":
                try:
                    data = json.loads(event["payload"].decode())
                    task_id = data.get("task_id")
                    if task_id in pending:
                        print(
                            f"[coordinator] task {task_id} done by {event.get('peer_name')}: {data.get('result')}"
                        )
                        pending.pop(task_id, None)
                except Exception as exc:
                    print(f"[coordinator] bad whisper: {exc}")

    evt_task = asyncio.create_task(handle_events())

    try:
        counter = 0
        while True:
            await asyncio.sleep(3)
            # wait until at least one worker present
            if not node.peers():
                print("[coordinator] no workers yet, skipping task")
                continue
            task_id = uuid.uuid4().hex[:6]
            payload = json.dumps(
                {
                    "task_id": task_id,
                    "op": "echo",
                    "data": f"hello-{counter}",
                    "ts": time.time(),
                }
            ).encode()
            pending[task_id] = {"payload": payload}
            counter += 1
            print(f"[coordinator] SHOUT task {task_id}")
            await node.shout(b"TASKS", payload)
            # simple backpressure
            if counter >= 5:
                await asyncio.sleep(6)
                break
    finally:
        run_task.cancel()
        evt_task.cancel()
        await asyncio.gather(run_task, evt_task, return_exceptions=True)
        await node.stop()
        print("[coordinator] stopped")


async def run_worker(name: str, port: int, interface: str | None, verbose: bool):
    node = ZreNode(f"worker-{name}")
    node.set_header("X-ROLE", "worker")
    node.set_header("X-WORKER", name)
    if port:
        node.set_port(port)
    if interface:
        node.set_interface(interface)
    if verbose:
        node.set_verbose(True)
    await node.start()
    run_task = asyncio.create_task(node.run())
    for _ in range(30):
        if node.peers():
            break
        await asyncio.sleep(0.2)
    await node.join(b"TASKS")
    print(f"[worker {name}] started, waiting for tasks...")

    async def handle_events():
        async for event in node.events():
            if event["type"] == "SHOUT" and event.get("group") == "TASKS":
                try:
                    data = json.loads(event["payload"].decode())
                    task_id = data.get("task_id")
                    print(f"[worker {name}] got task {task_id}: {data.get('data')}")
                    # simulate work
                    await asyncio.sleep(0.5)
                    result = {
                        "task_id": task_id,
                        "result": f"done-{data.get('data')}",
                        "worker": name,
                    }
                    await node.whisper(event["peer_id"], json.dumps(result).encode())
                    print(f"[worker {name}] whispered result for {task_id}")
                except Exception as exc:
                    print(f"[worker {name}] error: {exc}")

    try:
        await handle_events()
    finally:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        await node.stop()


def main():
    p = argparse.ArgumentParser(description="ZRE task queue")
    sub = p.add_subparsers(dest="mode", required=True)
    pc = sub.add_parser("coordinator", help="run coordinator")
    pc.add_argument("--port", type=int, default=5670)
    pc.add_argument("--interface", type=str, default=None)
    pc.add_argument("--verbose", action="store_true")
    pw = sub.add_parser("worker", help="run worker")
    pw.add_argument("name", help="worker name")
    pw.add_argument("--port", type=int, default=5670)
    pw.add_argument("--interface", type=str, default=None)
    pw.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    if args.mode == "coordinator":
        try:
            asyncio.run(run_coordinator(args.port, args.interface, args.verbose))
        except KeyboardInterrupt:
            print("\nInterrupted, shutting down...")
    else:
        try:
            asyncio.run(run_worker(args.name, args.port, args.interface, args.verbose))
        except KeyboardInterrupt:
            print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
