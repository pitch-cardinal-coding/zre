# ccalls — two C sensors + one Python app

Two libzyre `sensor` devices publish temperature readings to group `CHAT`;
a Python `app` prints them and broadcasts `cmd-refresh` commands back.
Every message flows in every direction, C↔Python, on one LAN or across
subnets.

Files:

- `sensor.c` — C device (builds against libzyre).
- `app.py` — Python app (uses this repo's `zre`).

## 1. Build the C sensor

On the machine(s) that run the C side. Needs libzyre built with
`--enable-drafts` (full steps in `docs/LIBZYRE_INTEROP.md` section 2;
Ubuntu 26.04 one-liner for deps):

```bash
sudo apt-get install -y --no-install-recommends \
  build-essential autoconf automake libtool pkg-config cmake \
  libzmq3-dev libczmq-dev uuid-dev libsodium-dev git ca-certificates
```

Then, with libzyre installed under `~/.local`, copy `sensor.c` from this
repo (`examples/ccalls/sensor.c`) to the C machine and compile it there:

```bash
export PKG_CONFIG_PATH=$HOME/.local/lib/pkgconfig:$PKG_CONFIG_PATH
export LD_LIBRARY_PATH=$HOME/.local/lib:$LD_LIBRARY_PATH
gcc -O2 -g -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fPIE \
  -Wall -Wextra -Wformat=2 -Wformat-security -Werror \
  -o sensor sensor.c $(pkg-config --cflags --libs libzyre) -lczmq \
  -pie -Wl,-z,relro,-z,now
```

Zero warnings with `-Wall -Wextra -Werror` is the bar: the flags above add
FORTIFY_SOURCE, stack protector, full RELRO + BIND_NOW and PIE (verified
with `readelf`: `GNU_RELRO`, `BIND_NOW`, `PIE`). Both C programs in this
repo build warning-free under them and run valgrind-clean (0 errors, 0
definite leaks — see `docs/LIBZYRE_INTEROP.md`).

## 2. Install the Python side

```bash
pip install zre
```

From a checkout of this repo instead:

```bash
pip install -e .
```

## 3. Run on one LAN

Use three terminals. Order does not matter. All three share UDP beacon
port 5670 (the default on both sides — that match is what makes
zero-configuration discovery work).

```bash
# terminal 1 (any machine on the LAN):
./sensor mobile-1 --port 5670 --every 3 --duration 60

# terminal 2 (same or another machine on the LAN):
./sensor mobile-2 --port 5670 --every 3 --duration 60

# terminal 3 (repo root for the checkout install):
python3 examples/ccalls/app.py --port 5670 --every 4 --duration 30
```

What you should see (ids differ):

- Each sensor prints `peer <name> entered` for the other two, then
  `published reading-21.3 #1` every 3 s and `command from pyapp:
  cmd-refresh #N` every 4 s.
- The app prints `reading from mobile-1: reading-21.3 #1`,
  `reading from mobile-2: ...`, plus `private from mobile-N: hi-from-C`
  greetings, and its own `sent cmd-refresh #N` lines.
- Stopping any peer (`Ctrl-C` or end of `--duration`) prints
  `peer <name> left` / `[app] peer <name> left` on the survivors.

## 4. Run across subnets (WAN)

Beacons do not cross routers, so pin the C side's TCP port and have the
app dial it. On the reachable machine:

```bash
./sensor mobile-1 --port 5670 --peer-port 19870 --duration 120
```

On the far machine (different beacon port isolates the clusters, so
discovery happens only through the dial):

```bash
python3 examples/ccalls/app.py --port 5680 --duration 60
# and in Python terms: await node.connect_peer("<sensor-ip>", 19870)
```

The current `app.py` discovers via beacons; to dial, add one line after
`run_task = ...` in `app.py`:

```python
await node.connect_peer("192.0.2.10", 19870)  # sensor's IP + --peer-port
```

After the HELLO exchange the dialled sensor is a normal peer
(`ENTER`/`SHOUT`/`WHISPER` all flow). Behind NAT, the sensor advertises
its public address with `zyre_set_advertised_endpoint(node,
"tcp://PUBLIC_IP:19870")` before `zyre_start`.

## 5. If it does not work

- Nothing discovers anything: beacon ports differ. Both default to 5670;
  check `--port` on all three.
- C prints `seq error` / drops the Python peer: version mismatch between
  the two sides — use the current `zre` release on the Python side.
- No `EXIT` when a C peer stops: program must call `zyre_stop()` before
  `zyre_destroy()` (`sensor.c` does).
- `EVASIVE <peer>` on idle peers is normal (5 s quiet probe), not a
  dropout. Only `left`/`EXIT` means gone.
- Two sensors on one host both bind UDP 5670: that is fine (SO_REUSEPORT),
  but give them different names.
