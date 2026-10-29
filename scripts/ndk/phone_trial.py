#!/usr/bin/env python3
"""Phone trial: talk to a libzyre C peer running on an Android device.

Run this on the PC (the Python zre side). The zc_peer binary must already
be on the phone (see README.md for the adb push step).

Curve is on by default, like in zc_peer. Keys come either from the
avedge key store (`--avedge [name]`: files `<name>.priv`/`<name>.pub`
with bare 40-char Z85 content, generated for example by
`make -C ~/devcode/avedge keys keys-check`) or from explicit
`--curve-pub/--curve-sec`. A missing pair is refused — there is no
implicit plain fallback.

`--plain` runs the Python side WITHOUT Curve/ZAP: this is the mode the
mobile app now runs (plain ZRE), and the only way to mesh with it.

Modes (mirroring the desktop interop matrix):
  beacon   pure UDP discovery, both sides broadcast on --port
  curve    same as beacon (kept explicit for tri-script clarity)
  gossip   Python hosts a ZGS hub on --hub-port; phone --gossip-connect it
  dial     direct TCP: Python dials the phone's fixed --peer-port inbox

Usage:
  python3 scripts/ndk/phone_trial.py beacon --avedge
  python3 scripts/ndk/phone_trial.py gossip --avedge --hub-port 19872
  python3 scripts/ndk/phone_trial.py gossip --plain --hub-port 19872
  python3 scripts/ndk/phone_trial.py dial --avedge --phone-ip 10.0.89.13
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zmq.auth import CURVE_ALLOW_ANY
from zmq.auth.thread import ThreadAuthenticator
from zmq.utils import z85

from zre import ZreNode

GROUP = "CHAT"


def load_avedge_keys(key_dir: Path, name: str) -> tuple[str, str]:
    """Read an avedge key pair: bare 40-char Z85, one per file."""
    priv = (key_dir / f"{name}.priv").read_text().strip()
    pub = (key_dir / f"{name}.pub").read_text().strip()
    z85.decode(pub)
    z85.decode(priv)  # raises ValueError on non-Z85 content
    return pub, priv


def emit_args(args) -> None:
    """Write the zc_peer command line for a trial mode to args.args_file.

    Used by the Makefile trial targets. The PHONE always gets its own
    pair, read from the avedge store (`--avedge <name>`, default
    phone1): building the file loads the keys once and never exposes
    z85 metacharacters to any shell.
    """
    pub, sec = load_avedge_keys(Path(args.avedge_dir).expanduser(), args.avedge)
    mode = args.trial_mode
    line = f"/data/local/tmp/zc_peer --name {args.phone_name} "
    if mode == "beacon":
        line += "--interface wlan0 "
    else:
        # gossip and dial bind a fixed inbox: libzyre binds the endpoint
        # (and stops beacons) when --endpoint is set
        line += f"--endpoint tcp://{args.phone_ip}:{args.peer_port} "
        if mode == "gossip":
            line += f"--gossip-connect tcp://{args.host_ip}:{args.hub_port} "
    line += f"--curve-pub {pub} --curve-sec {sec} --join {GROUP} "
    line += f"--shout {GROUP}:hello-secure-from-phone --shout-every 2 "
    line += "--duration 120 --verbose"
    Path(args.args_file).write_text(line + "\n")
    print(f"{args.args_file} written ({len(line)} bytes, mode={mode})")


def peer_headers(node: ZreNode, peer_id: str) -> dict[str, str]:
    """Decode the announced HELLO headers of a peer (X-IP, X-MAC, ...)."""
    peer = node._peers.get(peer_id)
    if not peer:
        return {}
    return {
        k.decode(errors="replace"): v.decode(errors="replace")
        for k, v in peer.headers.items()
    }


async def run_trial(args, dial: tuple[str, int] | None) -> bool:
    node = ZreNode(args.name)
    auth = None
    if not args.plain:
        if args.avedge:
            pub, sec = load_avedge_keys(Path(args.avedge_dir).expanduser(), args.avedge)
            node.set_zcert(pub, sec)
        else:
            node.set_zcert(args.curve_pub, args.curve_sec)
        # ZAP authenticator on the node context: starts BEFORE node.start()
        # and stops BEFORE node.stop() — the verified teardown order (see
        # docs/LIBZYRE_INTEROP.md section 7; the reverse order wedges the
        # shared context in term()).
        auth = ThreadAuthenticator(node._ctx)
        auth.start()
        auth.configure_curve(domain="*", location=CURVE_ALLOW_ANY)
    if dial is not None:
        node.set_port(args.port)
    if args.hub_port and args.mode == "gossip":
        node.gossip_bind(f"tcp://*:{args.hub_port}")
    await node.start()
    await node.join(GROUP)
    run_task = asyncio.create_task(node.run())
    if dial is not None:
        await node.connect_peer(*dial)
        print(f"PY dialed {dial[0]}:{dial[1]}", flush=True)
    seen = {"ENTER": False, "JOIN": False, "SHOUT": False}
    announced = ""
    ok = False
    try:
        for _ in range(int(args.wait * 2)):
            event = await node.recv(timeout=0.5)
            if event is None:
                continue
            print("PY", event, flush=True)
            etype = event["type"]
            if event.get("peer_name") != args.peer_name:
                continue
            if etype == "ENTER":
                seen["ENTER"] = True
                headers = peer_headers(node, event["peer_id"])
                announced = (
                    f"ip={headers.get('X-IP', '-')} mac={headers.get('X-MAC', '-')}"
                )
                if args.assisted_connect:
                    # One-way-NAT peer: the direct dial through the adb
                    # forward tunnel creates the missing socket leg. The
                    # deal is Curve-only on the phone: pass the key the
                    # peer announced in its HELLO (X-PUBLICKEY), else
                    # _do_connect refuses a plain dial and the tunnel
                    # never carries a frame.
                    parts = args.assisted_connect.rsplit(":", 2)
                    host = parts[1].strip("/ ")
                    port = int(parts[2])
                    key = headers.get("X-PUBLICKEY", "")
                    await node.connect_peer(host, port, public_key=key or None)
                    print(
                        f"PY assisted dial {host}:{port} "
                        f"pubkey={'provided' if key else 'MISSING'}",
                        flush=True,
                    )
                    print(f"PY assisted dial {host}:{port}", flush=True)
                await node.whispers(event["peer_id"], "hello-from-Python")
            elif etype == "JOIN":
                seen["JOIN"] = True
            elif etype == "SHOUT":
                seen["SHOUT"] = True
                await node.shouts(GROUP, "hello-back-from-Python")
            if all(seen.values()):
                break
        ok = (
            all(seen.values())
            and "ip=" in announced
            and "-" not in announced.split("ip=")[1].split()[0]
        )
        suffix = f" announce {announced}" if announced else ""
        print(f"{args.mode.upper()}_{'PASS' if ok else 'FAIL'}{suffix}", flush=True)
    finally:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        # Verified order: the ZAP authenticator FIRST, then the node — any
        # other order wedges the shared zmq context in term(). Plain mode
        # has no authenticator.
        if auth is not None:
            auth.stop()
        await node.stop()
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="phone <-> PC zre trial")
    parser.add_argument(
        "mode",
        choices=("beacon", "curve", "gossip", "dial", "build-args"),
        help="build-args: write the zc_peer args file for a trial mode instead of running a trial",
    )
    parser.add_argument("--name", default="pytrial")
    parser.add_argument(
        "--peer-name",
        default="phone1",
        help="expected peer node name (phone1 for the zc_peer trials; "
        "any string for app-uut trials — the app names its node after "
        "Build.MODEL, sanitized)",
    )
    parser.add_argument("--phone-ip", default="10.0.89.13", help="phone IP")
    parser.add_argument(
        "--assisted-connect",
        default=None,
        help="dial this endpoint when the expected peer ENTERs "
        "(one-way-NAT devices; the adb forward tunnel carries it)",
    )
    parser.add_argument(
        "--curve-pub", default=None, help="this node's Curve public key (z85 or raw)"
    )
    parser.add_argument(
        "--curve-sec", default=None, help="this node's Curve secret key (z85 or raw)"
    )
    parser.add_argument(
        "--avedge",
        nargs="?",
        const="avedge",
        default=None,
        metavar="NAME",
        help="load an avedge key pair (<NAME>.priv/.pub) instead of explicit keys; a bare --avedge uses the primary 'avedge' pair",
    )
    parser.add_argument(
        "--avedge-dir", default="~/.avedge", help="avedge key directory"
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="run PLAIN (no Curve/ZAP) — the mobile app's current mode; "
        "cannot be combined with any key flags",
    )
    parser.add_argument(
        "--peer-port",
        type=int,
        default=19870,
        help="fixed inbox port the phone binds (dial mode)",
    )
    parser.add_argument(
        "--hub-port",
        type=int,
        default=19872,
        help="ZGS hub port hosted here (gossip mode)",
    )
    parser.add_argument("--port", type=int, default=5670, help="UDP beacon port")
    parser.add_argument("--wait", type=float, default=40.0, help="seconds to try")
    parser.add_argument(
        "--trial-mode",
        choices=("beacon", "curve", "gossip", "dial"),
        default=None,
        help="with build-args: the trial mode to emit the phone args file for",
    )
    parser.add_argument(
        "--phone-name", default="phone1", help="zc_peer's --name for build-args"
    )
    parser.add_argument(
        "--args-file",
        default="args.txt",
        help="output file for build-args (base64-streamed by launch-phone)",
    )
    parser.add_argument(
        "--host-ip",
        default="10.0.89.21",
        help="this PC's IP as dialled by the phone (gossip mode)",
    )
    args = parser.parse_args()

    if args.mode == "build-args":
        if args.trial_mode is None:
            parser.error("build-args needs --trial-mode <beacon|curve|gossip|dial>")
        emit_args(args)
        return

    if args.plain:
        if (
            args.avedge is not None
            or args.curve_pub is not None
            or args.curve_sec is not None
        ):
            parser.error("--plain cannot be combined with Curve key flags")
    elif bool(args.curve_pub) != bool(args.curve_sec):
        parser.error("--curve-pub and --curve-sec go together")
    elif args.curve_pub is None and args.avedge is None:
        # Curve is on by default; refusing here beats a silently plain node.
        parser.error(
            "Curve keys required: --avedge [name], --curve-pub/--curve-sec, or --plain"
        )

    dial = {
        "beacon": None,
        "curve": None,
        "gossip": None,
        "dial": (args.phone_ip, args.peer_port),
    }
    ok = asyncio.run(run_trial(args, dial[args.mode]))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
