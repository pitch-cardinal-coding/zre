# zre Examples — Complete Testing Guide

This guide assumes **nothing**. It walks you through every example in a
deliberate order — from "two programs see each other" to encrypted chat,
file transfer, and cross-subnet links — with the exact commands to type,
what you should see on screen, and what to do when it doesn't work.

Every "you should see" block below is real output captured during verified
runs on two machines (a laptop and a VM on a different subnet), in both
directions.

---

## Contents

1. [What the examples are](#1-what-the-examples-are)
2. [Install (once)](#2-install-once)
3. [The four rules of every example](#3-the-four-rules-of-every-example)
4. [Peer ids: `peer_hex` and `--uuid`](#4-peer-ids-peer_hex-and---uuid)
5. [The tour, in order](#5-the-tour-in-order)
6. [Two-machine testing](#6-two-machine-testing)
7. [Cross-subnet testing (`wan_direct.py`)](#7-cross-subnet-testing-wan_directpy)
8. [Automated cross-network testing (all examples, both directions)](#8-automated-cross-network-testing-all-examples-both-directions)
9. [Troubleshooting](#9-troubleshooting)
10. [Cheat sheet](#10-cheat-sheet)

---

## 1. What the examples are

Every example is the same idea at heart: several independent programs find
each other on the network (no server, no config) and exchange messages.
Each one wraps that idea in a different skin:

| Order | Example | Skin | Roles |
|-------|---------|------|-------|
| 1 | `presence.py` | Who is online? | any number of named peers |
| 2 | `chat.py` | Chat room | any number of named peers |
| 3 | `secure_chat.py` | Encrypted chat room | peers sharing a secret key |
| 4 | `whiteboard.py` | Shared canvas | any number of peers |
| 5 | `file_transfer.py` | Send a file to one peer | receiver + sender |
| 6 | `media_stream.py` | Broadcast a video | receivers + sender |
| 7 | `sensor_network.py` | IoT sensors | sensors + aggregator |
| 8 | `task_queue.py` | Work distribution | coordinator + workers |
| 9 | `service_discovery.py` | Service registry | registry + services + client |
| 10 | `config_sync.py` | Shared settings | node-1 (writer) + watchers |
| 11 | `health_monitor.py` | Failure detection | any number of monitors |
| 12 | `distributed_lock.py` | Mutual exclusion | node-1 + node-2 |
| 13 | `game_sync.py` | Game state sync | server + clients |
| 14 | `fast_tick.py` | Instant discovery | registry + service |
| 15 | `wan_direct.py` | Across subnets | remote + local |
| 16 | `gossip_mesh.py` | Discovery without beacons | hub + join |
| 17 | `leader_election.py` | Group coordinator vote | any number of voters |
| 18 | `benchmark.py` | Measurements | self-contained |

Run them in this order the first time — each one teaches the concept the
next one uses.

---

## 2. Install (once)

You need Python 3.9+ and Linux or macOS.

```bash
cd zre                        # the repository folder
python3 -m venv .venv         # make an isolated Python
source .venv/bin/activate     # use it (do this in EVERY new terminal)
pip install -e .              # the zre library
pip install cryptography      # only needed for secure_chat.py
```

Check it worked:

```bash
python3 examples/presence.py --help
```

You should see the argument list, ending with something like:

```text
--port PORT   beacon UDP port (default 15670)
--interface IFACE
              network interface or IP
--verbose     enable verbose logging
```

If instead you get `No module named zre`, the venv is not active — run
`source .venv/bin/activate` in that terminal.

> **Note for every command below:** run Python with `-u` (unbuffered), e.g.
> `python3 -u examples/chat.py ...`. Without it, output can appear in
> batches late, and interactive examples look broken when they are not.

---

## 3. The four rules of every example

**Rule 1 — Everyone uses the same `--port`.**
The port is the "channel" peers shout on. Two programs with different
`--port` values will never meet. The default is 15670, but on a shared
machine something else may already use it — pick a free one (see below)
and give the **same** port to every terminal of a group.

Pick a free port before starting (example with 24100):

```bash
ss -ulnp | grep ':24100 ' && echo "BUSY - pick another" || echo "free"
```

No output line from the examples will conflict with TCP: peers connect on
random high ports unless you pin one (only `wan_direct.py` does).

**Rule 2 — Start the listener first, wait ~2 seconds, then the sender.**
Receivers, aggregators, registries, servers: start those first. They wait
patiently; senders that start too early can fire into an empty room.

**Rule 3 — On multi-homed machines, pin the network card.**
A machine with WiFi + Ethernet + VPNs + virtual bridges (check with
`ip -4 addr`) has many network cards. Beacons may leave through the wrong
one. Pin the card that both peers share:

```bash
ip -4 addr show                     # find the interface name and IP
python3 -u examples/chat.py alice --port 24100 --interface wlan0
# or pin by address:
python3 -u examples/chat.py alice --port 24100 --interface 192.168.1.20
```

If one peer is pinned and the other is not, discovery usually still
converges (either side hearing the other is enough), but pin **both** for
reliable results.

**Rule 4 — Ctrl-C stops an example cleanly.**
Peers notice the departure (`EXIT` events elsewhere). To force-kill
everything after a test session:

```bash
pkill -f "examples/"         # kills all running examples
```

---

## 4. Peer ids: `peer_hex` and `--uuid`

Every node has a **UUID** — 32 hex characters like
`cc16b36e10e0db535c3999c1869113c2`. Whispers ("private messages") and
file sends are addressed to this id, referred to as `peer_hex` in the
commands.

**Where do I find a peer's hex?** It is printed by every relevant example
at startup:

```text
[alice] UUID: cc16b36e10e0db535c3999c1869113c2 beacon_port=24100 inbox=36071
[receiver] UUID: cc16b36e10e0db535c3999c1869113c2  <- give THIS hex to the sender
```

Copy the whole 32 characters. For file transfer you can also discover it
live with `file_transfer.py list` (shown in the tour).

**Stable ids with `--uuid`.** Normally a new run = new random UUID, so old
hexes die with the process. Give a node a stable id instead — any label
you like (it is hashed internally) or a real 32-char hex:

```bash
python3 -u examples/chat.py alice --port 24100 --uuid alice-lab
```

Now this node is *always* discoverable as
`sha256("alice-lab")[:32]` — restart it and whispers to that hex keep
working. Use distinct labels for every peer (`alice-lab`, `rx-lab`, ...).

**Collision guard.** A stable uuid must be unique among *concurrently
running* peers. If you start a second node with a uuid that is already
live on the network, the node refuses to run and exits with:

```text
ERROR: another node with uuid <hex> responded on the network ...
```

Stop the other node first, or pick a different label. (Every node sharing
the duplicated uuid stops itself — keeping one alive would let whispers
route to either node unpredictably.)

> The library API is `node.set_uuid("32-char-hex")` — strict hex/bytes;
> the label hashing is an example convenience. Collisions raise
> `zre.UUIDCollisionError` from `run()`.

---

## 5. The tour, in order

Open **one terminal per role**, activate the venv in each
(`source .venv/bin/activate`), and run from the repo root.

### 5.1 presence.py — see peers appear

The hello-world: no groups, no messages, just "who is here".

Terminal 1:
```bash
python3 -u examples/presence.py alice --port 24100
```
Terminal 2 (within a few seconds):
```bash
python3 -u examples/presence.py bob --port 24100
```

You should see, in terminal 1:

```text
[presence alice] started — beacon 24100, id 6c9e6be94e2c4d0aa87d35e20f8c9a41
[presence alice] waiting for peers... (Ctrl-C to leave)
  + ENTER bob (bad032bc) now 1 peers
    JOIN bob -> ALL
[presence alice] 5s — 1 peers, own [b'ALL'], peers=['bad032bc8f4c40e5bf338729793bfb72']
```

And mirrored in terminal 2 (`+ ENTER alice ...`). Ctrl-C terminal 2 →
terminal 1 prints `- EXIT bob ... now 0 peers`.

Occasionally you will see `! EVASIVE bob (...)`. That is the heartbeat
probe ("peer quiet for 5 s, poking it"); it now prints at most once per
20 s per peer. `EXIT` is the only real departure signal.

### 5.2 chat.py — talk

Terminal 1:
```bash
python3 -u examples/chat.py alice --port 24100 --uuid alice-lab
```
Terminal 2:
```bash
python3 -u examples/chat.py bob --port 24100 --uuid bob-lab
```

Type into either terminal and press Enter — the line appears in both:

```text
  >> bob joined the chat
  >> bob joined CHAT
  bob: hello from bob
```

Private messages go to one peer by hex (Rule of thumb: the hex is the
other terminal's UUID line):

```text
/w <peer_hex> hello privately
```

You should see on the receiving side:

```text
  alice (private): hi bob, this is only for you
```

(The `--uuid` labels are optional here, but they make the `/w` hex stable
across restarts — worth the habit.)

### 5.3 secure_chat.py — talk, encrypted

Same as chat, but every message is ChaCha20-Poly1305 encrypted with a key
you both hold. Generate one and paste it into **both** commands:

```bash
openssl rand -hex 32          # run once, copy the output
```

Terminal 1:
```bash
python3 -u examples/secure_chat.py alice --port 24100 \
    --shared-key <the-64-hex-chars> --uuid alice-lab
```
Terminal 2:
```bash
python3 -u examples/secure_chat.py bob --port 24100 \
    --shared-key <the-64-hex-chars> --uuid bob-lab
```

Type a message in one; the other prints `alice: top secret hello`. If the
keys differ you get:

```text
[bob] Decrypt failed from alice: ... (both ends need the same --shared-key)
```

### 5.4 whiteboard.py — shared canvas

Peers type coordinates; everyone sees every stroke.

Terminal 1:
```bash
python3 -u examples/whiteboard.py alice --port 24100
```
Terminal 2:
```bash
python3 -u examples/whiteboard.py bob --port 24100
```

Type `100 200 red` in either one. Both show:

```text
[you] stroke (100.0,200.0) red
  stroke from alice: (100.0,200.0) red total=3
```

`--demo` auto-generates strokes (also used automatically when the input is
not a keyboard):

```bash
python3 -u examples/whiteboard.py alice --port 24100 --demo
```

### 5.5 file_transfer.py — send a file to one peer

Terminal 1 — the receiver (start first, note its UUID line):
```bash
python3 -u examples/file_transfer.py receive ./out --port 24100 --uuid rx-lab
```
```text
[rx-5fe58e] UUID: cc16b36e10e0db535c3999c1869113c2  <- give THIS hex to the sender
[receiver] saving files into out (beacon 24100, Ctrl-C to quit)
```

Terminal 2 (optional) — find receivers live, from anywhere on the network:
```bash
python3 -u examples/file_transfer.py list --port 24100
```
```text
Peers (copy the full 32-char hex after UUID: to use as <peer_hex>
in `send <peer_hex> <file>`):

  cc16b36e10e0db535c3999c1869113c2  file-xfer-rx-5fe58e
```

Terminal 3 — the sender:
```bash
python3 -u examples/file_transfer.py send <peer_hex> ./photo.jpg --port 24100
```
```text
[send] peer cc16b36e ready, sending ./photo.jpg
Sending photo.jpg (1548288 bytes) to cc16b36e...
Transfer complete: photo.jpg
```

The file lands in `./out/photo.jpg`. It is byte-identical — prove it:

```bash
sha256sum ./photo.jpg ./out/photo.jpg
```

### 5.6 media_stream.py — broadcast a video

SHOUTs an mp4 to everyone; receivers save a playable copy.

Terminal 1:
```bash
python3 -u examples/media_stream.py recv --out ./media_out --port 24100
```
Terminal 2:
```bash
python3 -u examples/media_stream.py send --file ./sample.mp4 --port 24100
```
```text
[send] MEDIA_START sample.mp4 2000000
[send] MEDIA_END sample.mp4 2000000 bytes in 0.1s (28.1 MB/s)
```
Receiver:
```text
[recv] MEDIA_START sample.mp4 2000000 bytes from media-send-1653
[recv] MEDIA_END sample.mp4 2000000/2000000 bytes
[recv] saved media_out/sample.mp4 — play with: mpv media_out/sample.mp4
```

Any mp4 works; `--list` shows files in `--dir` (default `~/Videos`).

### 5.7 sensor_network.py — IoT aggregation

Terminal 1 — the collector (start first):
```bash
python3 -u examples/sensor_network.py aggregator --port 24100
```
Terminal 2 — six simulated sensors:
```bash
python3 -u examples/sensor_network.py sensors --port 24100
```
```text
[temp-001] Started - Type: temperature, Location: room-101
...
```
Aggregator fills up:
```text
[main-aggregator] Reading from temp-001: 21.3 celsius
[main-aggregator] Reading from light-001: 233.5 lux
[main-aggregator] Reading from motion-001: 0 boolean
```

### 5.8 task_queue.py — distribute work

Terminal 1 — the coordinator (start first; it exits after 5 tasks):
```bash
python3 -u examples/task_queue.py coordinator --port 24100
```
Terminal 2 — a worker (add more workers in more terminals if you like):
```bash
python3 -u examples/task_queue.py worker w1 --port 24100
```
```text
[worker w1] got task 123323: hello-0
[worker w1] whispered result for 123323
```
Coordinator:
```text
[coordinator] SHOUT task 123323
[coordinator] task 123323 done by worker-w1: done-hello-0
```

### 5.9 service_discovery.py — service registry

Three roles: a registry watching the network, services announcing
themselves, a client listing them.

Terminal 1:
```bash
python3 -u examples/service_discovery.py registry --port 24100
```
Terminal 2:
```bash
python3 -u examples/service_discovery.py service my-svc api 8080 --port 24100
```
Terminal 3:
```bash
python3 -u examples/service_discovery.py client --port 24100
```
Client:
```text
  Found service: my-svc
  Service: my-svc (api) on port 8080
```
Registry:
```text
  DISCOVERED: my-svc (5c3a7d21)
  ANNOUNCEMENT from my-svc: my-svc:api:8080
```

### 5.10 config_sync.py — shared settings

Terminal 1 — the writer:
```bash
python3 -u examples/config_sync.py node-1 --port 24100
```
Terminal 2 — a watcher (start after node-1):
```bash
python3 -u examples/config_sync.py node-2 --port 24100
```
Watcher receives every change:
```text
[node-2] Config updated: cache_ttl = 300 (v1)
[node-2] DB URL changed: postgresql://localhost:5432/mydb
[node-2] Feature flags: {'new_ui': True, 'beta': False}
```

### 5.11 health_monitor.py — failure detection

Terminal 1:
```bash
python3 -u examples/health_monitor.py monitor-1 --port 24100
```
Terminal 2:
```bash
python3 -u examples/health_monitor.py monitor-2 --port 24100
```
```text
[Monitor] Peer joined: monitor-2 (a8efb151)
[monitor-1] Health monitor started (port 24100)
```
Ctrl-C monitor-2 → monitor-1 raises the alarm:
```text
[Monitor] Peer left: monitor-2 (a8efb151)
  ALERT: Peer monitor-2 (a8efb151) went offline!
```

### 5.12 distributed_lock.py — only one node works at a time

Start both roughly together (acquiring waits for peers so a lone node
does not self-grant a meaningless lock):

Terminal 1:
```bash
python3 -u examples/distributed_lock.py node-1 --port 24100
```
Terminal 2:
```bash
python3 -u examples/distributed_lock.py node-2 --port 24100
```
One of them wins first:
```text
[node-1] Lock manager started (port 24100)
[node-1] Got lock! Doing work...
[node-1] Released lock
```

### 5.13 game_sync.py — 20 Hz game state

Terminal 1 — the server (start first):
```bash
python3 -u examples/game_sync.py server --port 24100
```
Terminal 2 — a player:
```bash
python3 -u examples/game_sync.py client Alice --port 24100
```
Server:
```text
[Server] Game server started (port 24100)
[Server] Player Alice: x=..., y=... (first-seen state printed once)
```

### 5.14 fast_tick.py — instant discovery

Same discovery at a 10 ms beacon interval. Terminal 1:
```bash
python3 -u examples/fast_tick.py --role registry --port 24100
```
Terminal 2:
```bash
python3 -u examples/fast_tick.py --role service --port 24100
```
Both print the ENTER latency:
```text
[registry] ENTER service after 0.02 s
```

### 5.15 benchmark.py — measurements (no second terminal)

Spawns its own mesh in-process:

```bash
python3 -u examples/benchmark.py discovery --nodes 5 --port 24100
python3 -u examples/benchmark.py throughput --msgs 500 --size 1024 --port 24100
python3 -u examples/benchmark.py latency --pings 50 --port 24100
python3 -u examples/benchmark.py scalability --port 24100
```
```text
=== Discovery Benchmark: 5 nodes (port 24100) ===
All 5 nodes discovered each other in 0.15s
```

### 5.16 wan_direct.py — the special one (see next section)

Needs two machines/subnets to be interesting; covered below.

### 5.17 gossip_mesh.py — discovery without beacons

---

## 6. Two-machine testing

Beacons are LAN broadcasts, so **two machines on the same LAN need zero
extra config**: same `--port` on both, done. Everything in the tour works
verbatim — just run some roles on machine A and some on machine B.

Recommended pairing (the roles that were designed to be split):

| Example | Machine A (start first) | Machine B |
|---------|------------------------|-----------|
| presence | `presence.py alice --port 24100` | `presence.py bob --port 24100` |
| chat | `chat.py alice --port 24100 --uuid alice-lab` | `chat.py bob --port 24100 --uuid bob-lab` |
| secure_chat | `secure_chat.py alice --port 24100 --shared-key $KEY` | same with `$KEY` |
| whiteboard | `whiteboard.py alice --port 24100 --demo` | `whiteboard.py bob --port 24100 --demo` |
| file_transfer | `file_transfer.py receive ./out --port 24100 --uuid rx-lab` | `file_transfer.py list --port 24100`, then `send <hex> ./file --port 24100` |
| media_stream | `media_stream.py recv --out ./out --port 24100` | `media_stream.py send --file x.mp4 --port 24100` |
| sensor_network | `sensor_network.py aggregator --port 24100` | `sensor_network.py sensors --port 24100` |
| task_queue | `task_queue.py coordinator --port 24100` | `task_queue.py worker w1 --port 24100` |
| service_discovery | `registry` + `service` on A | `client` on B (or any mix) |
| config_sync | `config_sync.py node-1 --port 24100` | `config_sync.py node-2 --port 24100` |
| health_monitor | `health_monitor.py monitor-1 --port 24100` | `health_monitor.py monitor-2 --port 24100` |
| distributed_lock | `distributed_lock.py node-1 --port 24100` | `distributed_lock.py node-2 --port 24100` |
| game_sync | `game_sync.py server --port 24100` | `game_sync.py client Alice --port 24100` |
| fast_tick | `fast_tick.py --role registry --port 24100` | `fast_tick.py --role service --port 24100` |
| wan_direct | `wan_direct.py remote --listen-port 24230 --port 24240` | `wan_direct.py local --peer A:24230 --send hi --wait 25 --port 24250` |
| gossip_mesh | `gossip_mesh.py hub --port 24100 --hub-port 24101` | `gossip_mesh.py join --port 24100 --hub A:24101 --send hi` |
| leader_election | `leader_election.py alice --port 24100 --group WORKERS` | `leader_election.py bob --port 24100 --group WORKERS` |

All 16 role-splittable examples above were verified across a laptop and a
separate VM (different subnets, no shared broadcast domain — see the next
section for how that even worked), in **both** role directions.

**Machine-specific gotchas:**

- If machine B never sees machine A, pin the interface on **both** (Rule
  3) — the most common cause is beacons leaving via the wrong NIC or a
  VPN.
- Some Wi-Fi access points enable "client isolation": wireless clients
  cannot talk to each other directly. Put both machines on wired, or use
  `wan_direct.py` from the next section.
- A firewall on either machine must allow UDP on your chosen port
  (e.g. `sudo ufw allow 24100/udp`).

**Worked example — laptop + VM at 192.168.122.87:**

```bash
# on the VM: stable-id receiver
python3 -u examples/file_transfer.py receive ./out --port 24110 --uuid rx-lab

# on the laptop: find it, then send (works even across subnets here —
# see wan_direct below for when it does not)
python3 -u examples/file_transfer.py list --port 24110
python3 -u examples/file_transfer.py send <hex-from-list> ./photo.jpg --port 24110
```

---

## 7. Cross-subnet testing (`wan_direct.py`)

UDP broadcasts do not cross routers. When the two machines are on
**different subnets** (or a VPN/bridge separates them), beacons cannot
arrive and `connect` fails. `wan_direct.py` dials the far peer by address
instead — same script both ends:

- **remote** = the side with a stable address, pins its TCP port with
  `--listen-port`
- **local** = the side that dials, passes `--peer host:port`

Direction 1 — remote on machine B (192.168.122.87), local on machine A:

```bash
# machine B
python3 -u examples/wan_direct.py remote --listen-port 24230 --port 24240

# machine A (note: --interface is A's own NIC facing B; --port is only
# the local beacon, irrelevant across subnets but must not clash locally)
python3 -u examples/wan_direct.py local --peer 192.168.122.87:24230 \
    --send "Hello over there" --wait 25 --port 24250
```

Direction 2 — swap the roles (remote on A, local on B) to prove both ways:

```bash
# machine A (its LAN IP as seen by B: 192.168.8.232 here)
python3 -u examples/wan_direct.py remote --listen-port 24230 --port 24250 \
    --interface wlp0s20f3

# machine B
python3 -u examples/wan_direct.py local --peer 192.168.8.232:24230 \
    --send "Hello back" --wait 25 --port 24240
```

Healthy output on the local side:

```text
[local] READY inbox=39213 beacon=24250 uuid=414f19bc
[local] connecting to 192.168.122.87:24230
[local] ENTER remote tcp://192.168.122.87:24230
[local] SHOUT 'Hello over there'
```

and on the remote side:

```text
[remote] READY inbox=24230 beacon=24240 uuid=227c3d32
[remote] ENTER local tcp://192.168.8.232:39213
[remote] SHOUT from local: Hello over there
```

After the handshake the peer is a normal peer: `SHOUT`, `WHISPER`,
`JOIN/LEAVE`, `EVASIVE` heartbeats and `EXIT` all work. Extra flags:
`--advertised-endpoint tcp://PUBLIC_IP:24230` for NAT setups, `--group`
(default CHAT), repeatable `--peer` to dial several peers.

Both directions above were verified between a laptop (WiFi subnet) and a
libvirt VM (bridge subnet) with no shared broadcast domain.

> **Behind the scenes:** the LAN examples *also* survive this exact
> host↔VM setup on a libvirt bridge (both sides hear each other's
> all-ones broadcasts through the bridge), which is why the section-6
> matrix ran without `wan_direct`. Do not rely on that on other setups —
> if two machines are on different subnets, use `wan_direct.py`.

---

## 8. Automated cross-network testing (all examples, both directions)

Typing the whole two-machine matrix by hand is exactly what
`scripts/cross_smoke.py` automates: it runs **every example between the two
machines, in both role directions** (each role takes a turn going first),
checks chat/secure_chat delivery, verifies file and media transfers by
sha256, dials `wan_direct` across the subnets, and prints a PASS/FAIL
summary. Cleanup is marker-based: every process it launches carries
`--uuid xsmoke-…` and only that marker is killed, so nothing else on either
machine is touched.

### What you need

- Two machines that can reach each other (A = the one you type on, B = the
  remote one).
- **sshpass** on machine A (the script drives B over password-ssh).
- On machine B: this same checkout with a working venv at
  `<dir>/.venv/bin/python` (see section 2 — install once on B too).
- Free ports: 15 consecutive UDP ports plus 1 TCP at `+200`, starting at
  `--base-port` (default 24800). Both ends are checked before anything runs.

### Run it

On machine A:

```bash
python3 scripts/cross_smoke.py \
    --peer iam@192.168.122.87 \
    --password SECRET \
    --remote-dir '~/buffy-zre' \
    --host-iface virbr0 \
    --remote-iface enp1s0 \
    --first-addr 192.168.122.1
```

Flag roles: `--peer` is machine B (user@host), `--password` can come from
`SSHPASS_PW` instead, `--remote-dir` is the checkout path on machine B,
`--host-iface`/`--remote-iface` pin A's/B's NIC when either machine is
multi-homed, and `--first-addr` is A's IP *as seen from B* (it enables the
wan_direct test).

or the same via make:

```bash
make cross-smoke PEER=iam@192.168.122.87 PASSWORD=SECRET \
     REMOTE_DIR=~/buffy-zre HOST_IFACE=virbr0 FIRST_ADDR=192.168.122.1
```

### What it does, in order

1. **Preflight** — checks ssh to B, checks the venv exists on B, checks
   every UDP/TCP port is free on **both** machines, kills leftovers from
   any earlier run.
2. For each of the 17 examples: run it A-first, then B-first (role swap).
   Transfers are verified with sha256 on the receiving machine; chat and
   whisper are matched against the example's real output lines.
3. **Summary** — `== 36/36 passed` style table; non-zero exit code on any
   failure. With `--keep-logs` the per-process logs stay in
   `/tmp/zre-cross-smoke/` for inspection.

### Narrowing the run

```bash
--groups lan          # presence, chat, secure_chat, whiteboard, fast_tick
--groups coord        # sensor, health, task_queue, discovery, config, lock, game, leader_election
--groups data,wan     # file_transfer, media_stream, wan_direct, gossip_mesh
-k file_transfer,chat # only tests whose name contains one of these
--list-tests          # show the test registry and exit
--keep-logs           # keep /tmp/zre-cross-smoke/ for debugging
```

`wan_direct` only runs when `--first-addr` is given (the script needs to
know A's address from B's point of view to dial it). When in doubt, leave
`--groups all` and the wan test reports `skipped` with a hint.

Two rules the automation also enforces (worth repeating from earlier
sections): `--uuid` labels must be unique among *concurrently running*
peers, and `config_sync` only SHOUTs at set-time — the script always starts
the watcher before the writer.

---

## 9. Troubleshooting

| Symptom | Cause → fix |
|---------|-------------|
| Nothing happens, no `ENTER` anywhere | Different `--port` values (Rule 1). Port busy (check `ss -ulnp`). Firewall blocks UDP. Wi-Fi client isolation. VPN grabbing the route → try `--interface`, or `wan_direct.py`. |
| One side sees the other but not vice versa | Normal briefly (discovery converges via HELLO). If permanent: pin `--interface` on both (Rule 3). |
| Peers find each other, then nothing | You typed messages into a role that only listens (receiver/aggregator/registry/server). Check the role table. |
| Output appears in weird chunks / late | You forgot `-u` after `python3`. |
| Lines send before the peer exists | Started the sender too soon. Rule 2: listener first, 2 s, sender. |
| `whisper` / `/w` silently does nothing | Stale hex: the peer restarted and got a new UUID → note the new UUID line, or start both sides with stable `--uuid` labels. |
| `Peer <hex> not found` in file_transfer | Receiver not running, different `--port`, or wrong hex → run `file_transfer.py list --port <same>`. |
| `Decrypt failed` in secure_chat | Different `--shared-key` on the two ends → generate once, paste twice. |
| `Address already in use` | Another example (or another service) owns that port → pick another and keep Rule 1 in mind. |
| `X is quiet (heartbeat probe)` repeats | Not an error: heartbeat probing of an idle peer, printed at most once per 20 s per peer. `EXIT` is the departure signal. |
| Everything worked yesterday, nothing today | Something else took your port or joined your channel (another test run?). Check `ss -ulnp`, change port. |
| works on same machine, fails across machines | Usually firewall (allow UDP), client isolation, or different subnets → use `wan_direct.py`. |

Universal debug switch: add `--verbose` to any example to see the wire
traffic (beacons, HELLO, seq numbers) and the endpoint addresses peers
actually advertise.

---

## 10. Cheat sheet

```bash
source .venv/bin/activate                 # every terminal

ss -ulnp | grep ':24100 '                 # is my port free?

python3 -u examples/chat.py alice --port 24100 --uuid alice-lab
                                          # the universal shape:
                                          # -u, example, role/name,
                                          # same --port everywhere,
                                          # --uuid for stable ids
                                          # --interface <nic> if multi-homed

python3 -u examples/file_transfer.py list --port 24100
                                          # find peer hexes live

pkill -f "examples/"                      # end of session cleanup

# the whole two-machine matrix, automated (section 8):
python3 scripts/cross_smoke.py --peer user@B --password SECRET \
    --remote-dir '~/buffy-zre' --host-iface virbr0 --first-addr 192.168.122.1
```

Common flags on every example: `--port`, `--interface`, `--verbose`,
`--help`. Every example also accepts `--uuid` for a stable peer id —
except `benchmark.py`, which builds its own throwaway mesh (random ids
are correct there). On `sensor_network.py` the flag applies to the
aggregator; the six simulated sensors each get their own random id.
