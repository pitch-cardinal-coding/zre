# zre — Pure Python ZRE (RFC 36) Implementation

[![PyPI](https://img.shields.io/pypi/v/zre.svg)](https://pypi.org/project/zre/)
[![Python versions](https://img.shields.io/pypi/pyversions/zre.svg)](https://pypi.org/project/zre/)
[![License: MIT](https://img.shields.io/pypi/l/zre.svg)](docs/LICENSE)
[![CI](https://github.com/pitch-cardinal-coding/zre/actions/workflows/release.yml/badge.svg)](https://github.com/pitch-cardinal-coding/zre/actions/workflows/release.yml)

> **ZRE** implements the [ZeroMQ Realtime Exchange Protocol (RFC 36)](https://rfc.zeromq.org/spec/36/) for peer-to-peer discovery and messaging. No brokers, no servers — peers find each other on the LAN with zero configuration, or dial each other directly across subnets by address.

## Features

| Feature | Description |
|---------|-------------|
| **Zero-configuration** | No central servers, brokers, or admin |
| **Peer discovery** | Automatic via UDP broadcast beacons (port 15670), or direct dial via `connect_peer(host, port)` where broadcasts can't reach |
| **Group messaging** | Join/leave named groups, multicast via unicast |
| **Direct messaging** | Whisper to individual peers |
| **Stable peer ids** | Optional fixed UUID (`set_uuid`) so `peer_hex` survives restarts |
| **Heartbeating** | Automatic detection of peers going evasive, silent, or dead |
| **Language neutral** | Speaks RFC 36 on the wire — interoperates with any compliant peer |
| **Pure Python** | Single asyncio event loop, no threads, no C extensions |

Wire conformance details: [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Installation

Requires Python 3.9+ on Linux or macOS.

```bash
pip install zre
```

With optional dependencies:

| Extra | Provides | Install |
|-------|----------|---------|
| `secure` | `cryptography`, used by `examples/secure_chat.py` | `pip install "zre[secure]"` |
| `dev` | pytest, pytest-asyncio, ruff | `pip install "zre[dev]"` |

From a checkout of this repository:

```bash
git clone https://github.com/pitch-cardinal-coding/zre.git
cd zre
python3 -m venv .venv && source .venv/bin/activate
pip install -e .          # runtime
pip install -e .[dev]     # + development tools
```

`requirements.txt` carries pinned versions of the dependency stack for reproducible environments; it is not needed for a normal install.

## Quick Start

```python
import asyncio
from zre import ZreNode


async def main():
    node = ZreNode("my-app")
    node.set_port(15670)  # override beacon port to isolate clusters
    await node.start()
    await node.join("CHAT")

    # Must run beacon/ROUTER loop concurrently with events
    run_task = asyncio.create_task(node.run())

    async for event in node.events():
        print(event)
        if event["type"] == "ENTER":
            await node.shout("CHAT", b"Hello!")

    run_task.cancel()
    await node.stop()


asyncio.run(main())
```

## API Overview

```python
node = ZreNode("my-node")  # name optional (random otherwise)

# Headers shared with peers during discovery (HELLO)
node.set_header("X-ROLE", "worker")

# Configuration — all of it BEFORE start()
node.set_port(15670)  # UDP beacon port (cluster isolation)
node.set_interface("eth0")  # pin NIC (or an IP) on multi-homed hosts
node.set_interval(1000)  # beacon interval, ms
node.set_evasive_timeout(5000)  # peer quiet -> EVASIVE probes, ms
node.set_expired_timeout(30000)  # peer silent -> removed, ms
node.set_beacon_peer_port(9999)  # fixed TCP ROUTER port (default ephemeral)
node.set_advertised_endpoint("tcp://203.0.113.50:9999")  # NAT setups
node.set_uuid("32-char-hex-or-16-bytes")  # stable peer id (default random)
node.set_verbose()  # wire-level logging

# Duplicate-uuid guard: if another node with the same stable uuid is live
# on the network, run() raises zre.UUIDCollisionError and the node shuts
# itself down (stable uuids must be unique among concurrently running peers).

await node.start()  # bind ROUTER + beacon socket
run_task = asyncio.create_task(node.run())  # REQUIRED: drive the node

await node.join("CHAT")  # / leave(group)
await node.shout("CHAT", b"hi all")  # group message
await node.whisper(peer_hex, b"hi")  # direct message (peer id = UUID hex)
await node.connect_peer("10.0.0.7", 9999)  # direct dial, no beacons

async for event in node.events():  # or: event = await node.recv(timeout=1.0)
    ...
node.peers()  # list of peer UUID hexes
node.own_groups()  # groups this node joined

run_task.cancel()
await node.stop()
```

### Event types

| Event | Description | Fields |
|-------|-------------|--------|
| `ENTER` | New peer discovered | `peer_id`, `peer_name`, `address` |
| `EXIT` | Peer left network | `peer_id`, `peer_name` |
| `JOIN` / `LEAVE` | Peer joined/left group | `peer_id`, `peer_name`, `group` |
| `SHOUT` | Group message | `peer_id`, `peer_name`, `group`, `payload` |
| `WHISPER` | Direct message | `peer_id`, `peer_name`, `payload` |
| `EVASIVE` | Peer went quiet (probing started; emitted once per quiet episode, at most every 20 s — an idle-but-alive peer answers the probe and stays) | `peer_id`, `peer_name` |
| `COLLISION` | Another node with this node's stable uuid is live on the network; the node emits this, then stops itself | `detail` |

`EXIT` is the only reliable "peer is gone" signal. `COLLISION` is emitted
before `run()` raises `zre.UUIDCollisionError`, and `events()` keeps
draining until the queue is empty so late consumers still receive it.

## Examples

The [`examples/`](examples/README.md) directory contains 16 runnable
scenario demos — chat, encrypted chat, file transfer, media streaming,
sensor networks, task queues, service discovery, distributed locks, game
sync, benchmarks, and more.

**Start with the [examples testing guide](examples/README.md)** — a
step-by-step, zero-assumptions walkthrough: install, pick a free port,
run each example in order with exact commands and the output you should
see, plus two-machine and cross-subnet (WAN) recipes and a
troubleshooting table.

Direct connection across subnets (beacons cannot cross routers):

```python
await node.connect_peer("192.168.122.87", 19870)  # TCP + HELLO, no beacon
```

The far side pins a stable port with `set_beacon_peer_port(19870)`;
behind NAT, advertise your public address with
`set_advertised_endpoint("tcp://PUBLIC_IP:19870")`. After the HELLO
exchange the dialed peer is indistinguishable from a beacon-discovered
one. See [`examples/wan_direct.py`](examples/wan_direct.py) for a
runnable both-directions demo and [`docs/PROTOCOL.md`](docs/PROTOCOL.md)
for the handshake rules (no phantom peers, duplicate-HELLO storm guard).

## Testing

```bash
python3 -m pytest tests/ -v        # LAN + WAN protocol coverage (76 tests)
make test                          # same, via Makefile
make ci                            # format + lint + tests
```

On machines where another ZRE-speaking service already owns UDP 15670,
isolate the suite:

```bash
ZRE_TEST_PORT=24190 python3 -m pytest tests/ -q
```

### Cross-network smoke test

`scripts/cross_smoke.py` runs **every example between two machines, in both
role directions**, verifying chat delivery, file/media sha256, and WAN
dialing — the same matrix used to validate the examples:

```bash
# on machine A (local), against machine B over ssh:
python3 scripts/cross_smoke.py --peer user@B --password SECRET \
    --remote-dir '~/checkout' --host-iface virbr0 \
    --first-addr 192.168.122.1      # A's IP as seen by B (enables wan_direct)
```

Select subsets with `--groups lan,coord,data,wan` or `-k name`; use
`--base-port` to pick free UDP ports (15 consecutive + 1 TCP at +200 are
checked at preflight). Or via make:

```bash
make cross-smoke PEER=user@B PASSWORD=SECRET HOST_IFACE=virbr0 FIRST_ADDR=192.168.122.1
```

## Documentation

| Document | Contents |
|----------|----------|
| [examples/README.md](examples/README.md) | Step-by-step guide to every example (start here) |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | RFC 36 wire format, beacon/message layouts, conformance notes |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Event-loop design and internal structure |
| [docs/SCENARIOS.md](docs/SCENARIOS.md) | Use-case catalog mapped to examples |
| [docs/BENCHMARKS.md](docs/BENCHMARKS.md) | Performance results, receipts, methodology |
| [docs/MONITORING.md](docs/MONITORING.md) | py-spy/memray profiling, lint/CI, tmux demos |

## License

MIT — see [docs/LICENSE](docs/LICENSE). For contributing, changelog, and security
reporting, see [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md), [docs/CHANGELOG.md](docs/CHANGELOG.md),
and [docs/SECURITY.md](docs/SECURITY.md).

## References

- [RFC 36: ZeroMQ Realtime Exchange Protocol](https://rfc.zeromq.org/spec/36/)
- [ZeroMQ](https://zeromq.org/)

---

**Status**: Production-ready — 76 tests passing (LAN + WAN), all 16
examples verified live on two machines across subnets in both directions
(`scripts/cross_smoke.py`, 30/30).
