# Talking to libzyre (C) from Python zre — complete how-to

Goal: a **C program** built on libzyre and a **Python program** built on
this repo's `zre` package discover each other and exchange messages —
on the same LAN with zero configuration, and across subnets / WAN by
direct dial. Both directions (C→Python, Python→C) work for discovery,
group chat, direct messages, presence, elections and Curve encryption.

The two programs are independent processes. They can run on two machines,
two VMs, or two terminals on one machine. The recipe was verified with
the C side in an Ubuntu 26.04 VM and the Python side on the host, using
both the harness in `interop/` and the stock C tool `zpinger`.

Contents:

1. [What you need](#1-what-you-need)
2. [Machine A — build libzyre and write the C program](#2-machine-a--build-libzyre-and-write-the-c-program)
3. [Machine B — install `zre` and write the Python program](#3-machine-b--install-zre-and-write-the-python-program)
4. [Same LAN — zero-configuration discovery](#4-same-lan--zero-configuration-discovery)
5. [Groups, shouts and whispers](#5-groups-shouts-and-whispers)
6. [Across subnets / WAN — direct dial](#6-across-subnets--wan--direct-dial)
7. [Curve encryption](#7-curve-encryption)
8. [Troubleshooting](#8-troubleshooting)
9. [Appendix — lab notes: visor VM, harness, test matrices](#appendix--lab-notes-visor-vm-harness-test-matrices)

---

## 1. What you need

| | Machine A (C) | Machine B (Python) |
|---|---|---|
| OS | Any Linux with gcc (verified on Ubuntu 26.04) | Any Linux/macOS |
| Python | — | Python 3.10+ |
| Network | UDP + TCP reachability on LAN; for WAN, TCP one way (B→A) | same |
| Ports | UDP **5670** (ZRE discovery, IANA-assigned — both sides must use it) | same |

The default beacon port in `zre` is now 5670 (`zre/node.py: BEACON_PORT`),
matching libzyre's `ZRE_DISCOVERY_PORT`. If either side uses a different
port the two clusters never meet.

## 2. Machine A — build libzyre and write the C program

### 2.1 Install dependencies

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  build-essential autoconf automake libtool pkg-config cmake \
  libzmq3-dev libczmq-dev uuid-dev libsodium-dev \
  git ca-certificates
```

What each library is for:

- `libzmq3-dev` (libzmq 4.3.5): the ZeroMQ transport, CURVE crypto, Z85 helpers.
- `libczmq-dev` (czmq 4.2.1): actors libzyre is built on — `zbeacon`
  (UDP discovery), `zauth` (Curve authentication), `zcert` (keys).
- `uuid-dev`, `libsodium-dev`: UUID headers and the CURVE backend.
- The rest is the autotools build toolchain.

### 2.2 Compile libzyre

```bash
git clone https://github.com/zeromq/zyre.git
cd zyre
./autogen.sh
./configure --prefix=$HOME/.local --with-libzmq --enable-drafts
make -j4
make install
```

- `--enable-drafts` is **required**: without it the build omits
  `zyre_set_zcert`, `zyre_set_beacon_peer_port`, gossip CURVE support and
  the `SILENT` event.
- Everything installs under `~/.local`. Every shell that builds or runs
  against it needs:

```bash
export PKG_CONFIG_PATH=$HOME/.local/lib/pkgconfig:$PKG_CONFIG_PATH
export LD_LIBRARY_PATH=$HOME/.local/lib:$LD_LIBRARY_PATH
```

Sanity check with the stock presence tool:

```bash
timeout 8 ~/.local/bin/zpinger -v
# JOINs group GLOBAL, prints ENTER/SHOUT as peers arrive
```

### 2.3 Write the C program

Minimal peer (`cpeer.c`) — create, start, join, print events, shout back.
Only stable API plus one draft call (`zyre_set_beacon_peer_port`, available
because of `--enable-drafts`):

```c
#include <stdio.h>
#include <zyre.h>

int main (int argc, char *argv [])
{
    const char *name = argc > 1 ? argv[1] : "cpeer";
    zyre_t *node = zyre_new (name);
    zyre_set_port (node, 5670);            /* UDP discovery, must match Python */
    zyre_set_beacon_peer_port (node, 9999);/* fixed TCP inbox: lets WAN peers dial us */
    zyre_set_interval (node, 1000);        /* 1 s beacons (default 0 = send-once) */
    if (zyre_start (node))
        return 1;
    zyre_join (node, "CHAT");

    while (true) {
        zyre_event_t *event = zyre_event_new (node);
        if (!event)
            break;                         /* interrupted */
        const char *type = zyre_event_type (event);
        printf ("C %s peer=%s name=%s\n", type,
                zyre_event_peer_uuid (event),
                zyre_event_peer_name (event));
        fflush (stdout);
        if (streq (type, "ENTER"))
            zyre_whispers (node, zyre_event_peer_uuid (event), "hello-from-C");
        if (streq (type, "JOIN"))
            zyre_shouts (node, "CHAT", "hello-chat-from-C");
        if (streq (type, "SHOUT")) {
            /* payload is a zmsg_t: print its first frame */
            zmsg_t *msg = zyre_event_msg (event);
            char *text = zmsg_popstr (msg);
            printf ("C got SHOUT: %s\n", text ? text : "(binary)");
            zstr_free (&text);
        }
        zyre_event_destroy (&event);
    }
    zyre_stop (node);      /* polite EXIT beacon — destroy alone sends nothing */
    zyre_destroy (&node);
    return 0;
}
```

Compile (note the explicit `-lczmq`: `libzyre.pc` lists czmq under
`Requires.private`, so `pkg-config --libs` alone leaves `zauth_*` /
`zcert_*` unresolved):

```bash
gcc -O2 -g -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fPIE \
  -Wall -Wextra -Wformat=2 -Wformat-security -Werror \
  -o cpeer cpeer.c $(pkg-config --cflags --libs libzyre) -lczmq \
  -pie -Wl,-z,relro,-z,now
```

API notes — the installed `~/.local/include/zyre.h` differs from stale web
examples:

- `zyre_set_port`, `zyre_set_interface`, `zyre_set_zap_domain`,
  `zyre_set_contest_in_group`, `zyre_join`, `zyre_leave` take **plain
  strings/ints**; only `zyre_set_header`, `zyre_set_endpoint`,
  `zyre_gossip_bind/connect`, `zyre_shouts/whispers` are printf-style.
- Event payloads are `zmsg_t *` (`zyre_event_msg`), peer address is
  `zyre_event_peer_addr`.
- `zcert_new_from()` takes **raw 32-byte keys**, not Z85 text.
- `zyre_stop()` before `zyre_destroy()` or no `EXIT` is ever sent.

## 3. Machine B — install `zre` and write the Python program

```bash
pip install zre          # or: pip install -e .  from a checkout
pip install cryptography # only for examples/secure_chat.py
```

Minimal peer (`pypeer.py`) — the exact counterpart of `cpeer.c`:

```python
import asyncio
from zre import ZreNode

async def main():
    node = ZreNode("pypeer")
    # UDP 5670 and 1 s beacons are the defaults — nothing to set for LAN.
    await node.start()
    await node.join("CHAT")
    run_task = asyncio.create_task(node.run())  # REQUIRED: drives beacons/TCP

    async for event in node.events():
        print("PY", event, flush=True)
        if event["type"] == "ENTER":
            await node.whispers(event["peer_id"], "hello-from-Python")
        if event["type"] == "SHOUT":
            await node.shouts("CHAT", "hello-back-from-Python")
            break

    run_task.cancel()
    await node.stop()

asyncio.run(main())
```

Run it: `python3 pypeer.py`. The `run()` task must keep running alongside
`events()` — without it no beacons are sent and no TCP is serviced.

## 4. Same LAN — zero-configuration discovery

Start both programs anywhere on the same broadcast LAN (order does not
matter). Each broadcasts a 22-byte beacon on UDP 5670
(`ZRE\x01` + 16-byte UUID + 2-byte TCP port); on receipt each dials the
other's TCP inbox and the two HELLOs complete the handshake.

Expected output (ids differ per run):

```
# machine A (C)                     # machine B (Python)
C ENTER peer=... name=pypeer        PY {'type': 'ENTER', ... 'peer_name': 'cpeer', ...}
C JOIN peer=... group=CHAT          PY {'type': 'JOIN', ... 'group': 'CHAT', ...}
C WHISPER ... hello-from-Python     PY {'type': 'WHISPER', ... 'payload': b'hello-from-C'}
```

If nothing appears: both sides must show UDP 5670 listening and broadcast
traffic flowing (`ss -lunp | grep 5670`, `tcpdump -n udp port 5670`). See
section 8.

## 5. Groups, shouts and whispers

Groups are just names; either side creates one by joining:

```c
/* C */
zyre_join (node, "SENSORS");
zyre_shouts (node, "SENSORS", "temp=21.5");
zyre_whispers (node, peer_uuid_string, "private");
```

```python
# Python
await node.join("SENSORS")
await node.shouts("SENSORS", "temp=21.5")
await node.whispers(peer_id, "private")
```

`SHOUT` is multicast-via-unicast (one TCP send per member). `WHISPER`
needs the peer id — the UUID hex in the `ENTER` event. Presence is
`ENTER` (arrived), `JOIN`/`LEAVE` (group changes), `EXIT` (clean stop via
`zyre_stop()` / `node.stop()`), `EVASIVE` (quiet for ~5 s, probed with
`PING` — normal for idle peers, not a dropout).

## 6. Across subnets / WAN — direct dial

Beacons do not cross routers, so the WAN side dials a known TCP address.
Pin the C program's inbox port (already done in 2.3 with
`zyre_set_beacon_peer_port(node, 9999)`), then on the Python side:

```python
node = ZreNode("wan-node")
await node.start()
await node.join("CHAT")
run_task = asyncio.create_task(node.run())
await node.connect_peer("192.0.2.10", 9999)  # C machine's IP + pinned port
```

After the HELLO exchange the dialled peer is indistinguishable from a
beacon-discovered one (`ENTER`/`JOIN`/`SHOUT`/`WHISPER` all flow).
Behind NAT, the dialled side advertises its public address instead:

```python
node.set_advertised_endpoint("tcp://203.0.113.50:9999")  # before start()
```

`examples/wan_direct.py` is a runnable both-directions demo of this
pattern. (Gossip mode — `gossip_bind`/`gossip_connect` — now speaks
czmq's ZGS.1 `zgossip` wire protocol and is verified against libzyre on
Android; plain beacons and `connect_peer` dialing still work against
libzyre the same way.)

## 7. Curve encryption

Generate one keypair per side (either side, e.g. Python):

```python
import zmq
pub, sec = zmq.curve_keypair()   # 40-char z85 each
```

C side (`--enable-drafts` build):

```c
zactor_t *auth = zactor_new (zauth, NULL);
zstr_sendx (auth, "CURVE", CURVE_ALLOW_ANY, NULL);
zcert_t *cert = zcert_new_from (raw_pub_32B, raw_sec_32B); /* RAW bytes */
zyre_set_zcert (node, cert);
```

Python side — own cert plus a ZAP authenticator on the node context:

```python
import zmq.auth
from zmq.auth.thread import ThreadAuthenticator
node.set_zcert(pub, sec)     # raw 32 B or 40-char z85
await node.start()
auth = ThreadAuthenticator(node._ctx)  # NOT zmq.auth.Authenticator,
auth.start()                           # which never answers ZAP here
auth.configure_curve(domain="*", location=zmq.auth.CURVE_ALLOW_ANY)
# ... run ...
auth.stop()          # first ...
await node.stop()    # ... then this (reverse order raises ContextTerminated)
```

Curve nodes broadcast 54-byte v3 beacons (key appended) and require the
`X-PUBLICKEY` header on HELLO — `zre` sends it from `set_zcert`
automatically. A mixed plain/Curve cluster splits in two by design
(downgrade protection), same as pure libzyre. Verified end-to-end against
a libzyre 2.0.1 peer on Android too (`scripts/ndk/README.md` CURVE
trial): v3 beacons both directions, the peer's `zauth` logging
`ZAP request mechanism=CURVE` and allowing the Python cert.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No `ENTER` either side | Beacon ports differ | Both sides must use UDP 5670: `set_port(5670)` / `zyre_set_port(node, 5670)` |
| No `ENTER`, one host multi-homed | Limited broadcast leaves via the default route only | Use current `zre`, which sends every subnet broadcast |
| `seq error`, peer dropped (C log) | `zre`/libzyre version mismatch | Use matching current versions on both sides |
| Elections do not converge / duplicate `LEADER` | `zre`/libzyre version mismatch | Use matching current versions on both sides |
| Python connects but no HELLO arrives (Curve) | `zmq.auth.Authenticator` instead of thread version | Use `ThreadAuthenticator` on `node._ctx` |
| No `EXIT` when C stops | `zyre_destroy()` without `zyre_stop()` | Call `zyre_stop()` first |
| C peers `EVASIVE` while idle | Normal quiet probe (default interval 0 = beacons send-once), not a dropout | `zyre_set_interval(node, 1000)`; only `left`/`EXIT` means gone |
| `secure_chat` exits `cryptography not installed` | Missing optional dep on one side | `pip install cryptography` on **both** machines |
| `zmq_z85_decode` fails / zero key beaconed | `zcert_new_from` given Z85 text | Decode to 32 raw bytes first |
| Valgrind reports leaks in own code | Unfreed copies / `strncat` on binary frames | Free argv copies at exit; copy frames with `memcpy` |

### Production builds and leak checks (both sides)

Build every C program with the hardened flags from sections 2.3/4
(zero warnings; `readelf` shows `GNU_RELRO` + `BIND_NOW` + `PIE`):

- `zc_peer` + a peer, and `sensor` + a peer, each run as a valgrind pair
  (valgrind-3.26.0, `--leak-check=full --show-leak-kinds=definite`):
  **0 errors, 0 bytes definitely lost on all four processes**.
- Python soak: 21 × full lifecycle (two nodes, beacon dial, join/leave,
  shout/whisper, election contest, stop) — live `ZreNode`/`Peer`/`Group`
  counts flat, RSS +0.5 MB, open FDs flat at 7 across repeated run/stop
  cycles.

---

## Appendix — lab notes: visor VM, harness, test matrices

### Visor VM specifics

The recipe above was executed with the C side in the visor follower VM
(Ubuntu 26.04, interface `enp0s2`, DHCP on `virbr0` — this run
`192.168.122.87`, the lease can move) and Python on the host:

```bash
cd ~/devcode/visor
make vm-ip     # re-resolve the lease when it moves
make vm-ssh    # login: user iam, password = sha256("visor-<mac-no-colons>")[:12]
```

### Harness (`interop/zc_peer.c`)

`zpinger` is fixed-group/fixed-behaviour, so the repo carries `zc_peer`,
which prints one `EVENT ...` line per event:

```
--name --beacon-port (5670) --beacon-peer-port (fixed TCP inbox for WAN
        dialling) --interface --endpoint
--gossip-bind/--gossip-connect --join (repeatable) --leave-after
--contest (repeatable) --shout "GRP:TEXT" --shout-every/--shout-delay
--whisper-on-enter --announce NAME=VAL --curve-pub/--curve-sec
(Curve is ALWAYS on: keys optional per peer, no plain mode)
--zap-domain --duration --verbose
```

Build in the VM (with the section-2 exports set):

```bash
gcc -O2 -g -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fPIE \
  -Wall -Wextra -Wformat=2 -Wformat-security -Werror \
  -o ~/zyre-peer/zc_peer ~/zc_peer.c \
  $(pkg-config --cflags --libs libzyre) -lczmq -lzmq \
  -pie -Wl,-z,relro,-z,now
```

### Verified matrices (this run, host .1 ↔ VM .87, UDP 5670)

C ↔ Python: discovery, `JOIN`/`SHOUT` both ways, `WHISPER` on `ENTER`
both ways, `LEAVE`, clean-stop `EXIT` both ways, 2 C + 1 Python SHOUT
mesh, elections with Python smallest and largest id (one `LEADER` each
side, all agree), full Curve exchange (`ENTER`/`JOIN`/`SHOUT`/`WHISPER`/
`PING_OK`/`LEAVE`/`EXIT`, ZAP `200 OK`).

Python ↔ Python `scripts/cross_smoke.py` across host/VM, both role
directions: `lan` 12/12, `coord` 16/16, `data` 4/4 (sha256-verified),
`wan` 4/4 (`wan_direct` dial + `gossip_mesh` hub; one gossip shout miss
under load did not reproduce in isolation). VM Python setup:

```bash
sudo apt-get install -y python3-venv python3-pip
# unpack the repo to ~/pyzre, then:
python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/pip install cryptography
```

`pytest tests/`: 99/99 pass; `ruff check` and `ruff format --check` clean.
