# zre Examples

Every example below runs live — each was validated host to bridge VM
except `benchmark.py`, which spawns its mesh in-process and runs local.
Common flags: `--port` (beacon port, isolates runs), `--interface`
(pin beacons to a subnet on multi-homed hosts), `--verbose`, `--help`.

Run from the repo root with the zre venv:

```bash
/home/iam/devcode/.env/zre/bin/python3 examples/<name>.py ...
```

## Discovery & Presence

### service_discovery.py — service registry pattern
Registry tracks services; services announce; clients list. Announcements
re-fire every 5 s so late joiners always catch one.

```bash
python3 examples/service_discovery.py registry --port 15670 --interface virbr0
python3 examples/service_discovery.py service my-svc api 8080 --port 15670
python3 examples/service_discovery.py client --port 15670
```

### presence.py — presence tracker
Beacon-only presence table with ENTER/EXIT/EVASIVE, no group needed.

```bash
python3 examples/presence.py alice --port 15670
python3 examples/presence.py bob --port 15670
```

### fast_tick.py — 10 ms beacon tick demo
Two nodes discovering at `set_interval(10)`; prints ENTER latency.
Proven host to VM: ENTER 0.00 s / ~2.6 s, single-digit CPU.

```bash
python3 examples/fast_tick.py --role registry --port 14056 --interface virbr0
python3 examples/fast_tick.py --role service --port 14056 --interface enp0s2
```

## Messaging

### chat.py — interactive chat room
SHOUT to `CHAT`, `/w <peer_hex> <msg>` for private. Needs `-u` for live
output; delay piped stdin past discovery or lines send too early.

```bash
python3 -u examples/chat.py alice --port 15670 --interface virbr0
```

### whiteboard.py — collaborative whiteboard
SHOUT JSON strokes. `--demo` auto-generates when stdin is not a tty.

```bash
python3 examples/whiteboard.py alice --port 15670 --demo
python3 examples/whiteboard.py bob --port 15670 --demo
```

### secure_chat.py — encrypted messaging
ChaCha20-Poly1305 via `cryptography`. Both ends need the SAME
`--shared-key` or every message fails to decrypt (the error says so).

```bash
python3 examples/secure_chat.py alice --port 15670 --shared-key <64-hex>
```

## Data & Files

### file_transfer.py — P2P file sharing
WHISPER 64 KiB chunks; receiver reassembles. Needs the receiver's peer
hex (discover first). Proven byte-identical (`sha256`) host to VM.

```bash
python3 examples/file_transfer.py receive ./out --port 15670
python3 examples/file_transfer.py send <peer_hex> ./sample.mp4 --port 15670
```

### media_stream.py — media streaming
Broadcasts an mp4 over SHOUT; receiver saves byte-identical output.
Proven with a 3 MiB file host to VM.

```bash
python3 examples/media_stream.py recv --out ./media_out --port 15670
python3 examples/media_stream.py send --file ./sample.mp4 --port 15670
```

### sensor_network.py — IoT sensor aggregation
`sensors` publish readings; `aggregator` prints them live.

```bash
python3 examples/sensor_network.py aggregator --port 15670 --interface virbr0
python3 examples/sensor_network.py sensors --port 15670
```

## Coordination

### distributed_lock.py — distributed locking
Majority-vote lock with grants over WHISPER. Grants are counted and
acquire waits for peers first — without both, staggered starts
self-grant with no coordination.

```bash
python3 examples/distributed_lock.py node-1 --port 15670 --interface virbr0
python3 examples/distributed_lock.py node-2 --port 15670
```

### task_queue.py — distributed task queue
Coordinator SHOUTs tasks, workers WHISPER results back.

```bash
python3 examples/task_queue.py coordinator --port 15670 --interface virbr0
python3 examples/task_queue.py worker w1 --port 15670
```

### config_sync.py — distributed config propagation
`node-1` sets values; watchers fire on every peer. Version vectors
resolve conflicts.

```bash
python3 examples/config_sync.py node-1 --port 15670 --interface virbr0
python3 examples/config_sync.py node-2 --port 15670
```

### health_monitor.py — peer health monitoring
Tracks ENTER/JOIN plus EVASIVE/EXIT per peer.

```bash
python3 examples/health_monitor.py monitor-1 --port 15670 --interface virbr0
python3 examples/health_monitor.py monitor-2 --port 15670
```

## State & Benchmarks

### game_sync.py — multiplayer state sync
Server collects player states; clients publish at 20 Hz. The server
prints each player once on first-seen state.

```bash
python3 examples/game_sync.py server --port 15670 --interface virbr0
python3 examples/game_sync.py client Alice --port 15670
```

### benchmark.py — discovery/throughput/latency/scale
Single-host mesh benchmarks (no LAN split; nodes spawn in-process).

```bash
python3 examples/benchmark.py discovery --nodes 5 --port 15670
python3 examples/benchmark.py throughput --msgs 500 --size 1024 --port 15670
python3 examples/benchmark.py latency --pings 50 --port 15670
python3 examples/benchmark.py scalability --port 15670
```
