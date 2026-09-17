"""Group elections: LEADER convergence and re-election after leader loss."""

import asyncio
import time

from zre.node import ZreNode

UUIDS = ("11" * 16, "22" * 16, "33" * 16)
PORTS = (24911, 24912, 24913)


async def collect(node, seconds=1.0):
    deadline = time.monotonic() + seconds
    found = []
    while time.monotonic() < deadline:
        while not node._event_queue.empty():
            found.append(node._event_queue.get_nowait())
        await asyncio.sleep(0.05)
    return found


async def meshed_trio():
    nodes = []
    tasks = []
    for name, uuid_hex, port in zip(("n1", "n2", "n3"), UUIDS, PORTS):
        node = ZreNode(name)
        node.set_uuid(uuid_hex)
        node.set_beacon_peer_port(port)
        node.set_contest_in_group("VOTE")
        await node.start()
        nodes.append(node)
    for node in nodes:
        tasks.append(asyncio.create_task(node.run()))
    for node, port in zip(nodes, PORTS):
        for other_port in PORTS:
            if other_port != port:
                await node.connect_peer("127.0.0.1", other_port)
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if all(len(node.peers()) >= 2 for node in nodes):
            break
        await asyncio.sleep(0.1)
    assert all(len(node.peers()) >= 2 for node in nodes)
    return nodes, tasks


async def shutdown(nodes, tasks):
    for node in nodes:
        node._running = False
    await asyncio.sleep(0.3)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for node in nodes:
        await node.stop()


async def last_leaders(nodes, timeout=20.0):
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        done = True
        for node in nodes:
            while not node._event_queue.empty():
                event = node._event_queue.get_nowait()
                if event.get("type") == "LEADER" and event.get("group") == "VOTE":
                    last[node.name.decode()] = event["peer_id"]
            if node.name.decode() not in last:
                done = False
        if done and len(last) == len(nodes):
            await asyncio.sleep(2.0)
            for node in nodes:
                while not node._event_queue.empty():
                    event = node._event_queue.get_nowait()
                    if event.get("type") == "LEADER" and event.get("group") == "VOTE":
                        last[node.name.decode()] = event["peer_id"]
            return last
        await asyncio.sleep(0.1)
    return last


class TestElections:
    async def test_lowest_uuid_wins(self):
        nodes, tasks = await meshed_trio()
        try:
            for node in nodes:
                await node.join("VOTE")
            leaders = await last_leaders(nodes)
            assert len(leaders) == 3
            assert set(leaders.values()) == {UUIDS[0]}
        finally:
            await shutdown(nodes, tasks)

    async def test_re_election_after_leader_leaves(self):
        nodes, tasks = await meshed_trio()
        try:
            for node in nodes:
                await node.join("VOTE")
            leaders = await last_leaders(nodes)
            assert set(leaders.values()) == {UUIDS[0]}
            gone, gone_task = nodes[0], tasks[0]
            gone._running = False
            await asyncio.sleep(0.5)
            gone_task.cancel()
            await asyncio.gather(gone_task, return_exceptions=True)
            await gone.stop()
            survivors = nodes[1:]
            leaders = await last_leaders(survivors)
            assert set(leaders.values()) == {UUIDS[1]}
        finally:
            await shutdown(nodes[1:], tasks[1:])

    async def test_no_contest_means_no_leader_event(self):
        first = ZreNode("quiet-a")
        second = ZreNode("quiet-b")
        first.set_beacon_peer_port(24914)
        second.set_beacon_peer_port(24915)
        await first.start()
        await second.start()
        first_task = asyncio.create_task(first.run())
        second_task = asyncio.create_task(second.run())
        try:
            await first.connect_peer("127.0.0.1", 24915)
            await asyncio.sleep(3.0)
            await first.join("CALM")
            await second.join("CALM")
            events = await collect(first, seconds=4.0)
            assert [event for event in events if event.get("type") == "LEADER"] == []
        finally:
            first._running = False
            second._running = False
            await asyncio.sleep(0.3)
            first_task.cancel()
            second_task.cancel()
            await asyncio.gather(first_task, second_task, return_exceptions=True)
            await first.stop()
            await second.stop()
