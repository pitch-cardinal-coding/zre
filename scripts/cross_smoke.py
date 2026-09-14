#!/usr/bin/env python3
"""Cross-network smoke test: run every example between two hosts, both ways.

Automates the two-host matrix (e.g. a laptop and a VM on different subnets):
each example is run once with role A on the local machine and role B on the
remote host, then with the roles swapped. File transfers and media streams
are verified with sha256 on the receiving side.

The remote side is driven over SSH (password via --password or SSHPASS_PW
env; uses sshpass). Both machines need a working checkout with a venv:

    local:  repo root; the script runs examples with sys.executable
    remote: --remote-dir containing .venv/bin/python and examples/

Usage:
    python3 scripts/cross_smoke.py --peer user@192.168.122.87 \
        --password SECRET --remote-dir '~/buffy-zre' \
        [--host-iface virbr0] [--remote-iface enp0s2] [--base-port 24800]
        [--first-addr 192.168.122.1] [--groups all|lan|coord|data|wan]
        [-k substring] [--list-tests] [--keep-logs]

--first-addr is the IP of the LOCAL machine as seen from the remote side; it
enables the wan_direct test, which listens on TCP base-port+200 on the local
machine and dials it from the remote.

Requires sshpass locally and free UDP ports on both ends (15 ports starting
at --base-port, plus one TCP port at base-port+200 — checked at preflight).

Every launched example carries ``--uuid xsmoke-<rid>``; cleanup pkills that
marker (with a bracket trick so the pattern never matches its own shell).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = Path("/tmp/zre-cross-smoke")

UUID_PREFIX = "xsmoke-"


def log(msg: str) -> None:
    print(msg, flush=True)


def label_hex(label: str) -> str:
    """The peer hex an example derives from --uuid <label> (see _common.parse_uuid)."""
    return hashlib.sha256(label.encode()).hexdigest()[:32]


class Side:
    """One machine: runs example commands, reads files, computes hashes."""

    name = "side"

    def __init__(self, iface: str | None, timeout_scale: float = 1.0):
        self.iface = iface
        self.timeout_scale = timeout_scale

    # -- command construction -------------------------------------------------
    def example_cmd(self, example: str, argv: list[str], rid: str, timeout: int) -> str:
        """Shell command to run an example with our cleanup marker."""
        scaled_timeout = int(timeout * self.timeout_scale)
        if self.iface and "--interface" not in argv:
            argv = [*argv, "--interface", self.iface]
        argv = [*argv, "--uuid", f"{UUID_PREFIX}{rid}"]
        return f"timeout {scaled_timeout} {self.python} -u examples/{example}.py {self.argv_str(argv)}"

    @staticmethod
    def argv_str(argv: list[str]) -> str:
        return " ".join(shlex.quote(a) for a in argv)

    # -- primitives (overridden) ---------------------------------------------
    @property
    def python(self) -> str:
        return sys.executable

    def run_fg(self, cmd: str, timeout: int) -> tuple[int, str]:
        raise NotImplementedError

    def run_bg(self, cmd: str, logname: str) -> None:
        raise NotImplementedError

    def pkill(self, regex: str) -> None:
        raise NotImplementedError

    def read_file(self, path: str) -> str:
        raise NotImplementedError

    def sha256(self, path: str) -> str:
        raise NotImplementedError

    def rm_rf(self, path: str) -> None:
        self.run_fg(f"rm -rf {shlex.quote(path)}", 15)

    # -- helpers ---------------------------------------------------------------
    def pkill_marker(self, rid: str) -> None:
        # Bracket trick: the pattern must not match the executing shell itself.
        # Matches "--uuid xsmoke-<rid>" on the example's command line.
        self.pkill(f"--uui[d] {UUID_PREFIX}{rid}")

    def start_example(
        self, example: str, argv: list[str], rid: str, timeout: int
    ) -> None:
        lp = self.log_path(rid)
        # Never let a previous run's log answer wait_log.
        self.rm_rf(lp)
        self.run_bg(self.example_cmd(example, argv, rid, timeout), f"xsmoke-{rid}.log")

    def wait_log(self, path: str, pattern: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pattern in self.read_file(path):
                return True
            time.sleep(0.5)
        return False

    def log_path(self, rid: str) -> str:
        return f"{self.remote_log_dir}/xsmoke-{rid}.log"


class Local(Side):
    name = "local"

    def __init__(self, iface: str | None, log_dir: Path):
        super().__init__(iface)
        self.log_dir = log_dir
        self.procs: list[subprocess.Popen] = []

    @property
    def remote_log_dir(self) -> str:
        return str(self.log_dir)

    def run_fg(self, cmd: str, timeout: int) -> tuple[int, str]:
        process = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout + 10
        )
        return process.returncode, process.stdout + process.stderr

    def run_bg(self, cmd: str, logname: str) -> None:
        path = self.log_dir / logname
        fh = open(path, "w")  # noqa: SIM115 — lives as long as the child
        process = subprocess.Popen(
            cmd, shell=True, stdout=fh, stderr=fh, cwd=REPO, stdin=subprocess.DEVNULL
        )
        self.procs.append(process)
        # The child keeps its own dup of the fd.
        fh.close()

    def pkill(self, regex: str) -> None:
        subprocess.run(["pkill", "-f", "--", regex], check=False)

    def read_file(self, path: str) -> str:
        try:
            return Path(path).read_text(errors="replace")
        except OSError:
            return ""

    def sha256(self, path: str) -> str:
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 16), b""):
                    digest.update(chunk)
        except OSError:
            return ""
        return digest.hexdigest()

    def write_bytes(self, path: str, size: int) -> str:
        data = os.urandom(size)
        Path(path).write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def kill_all(self) -> None:
        for p in self.procs:
            p.kill()
        self.procs.clear()


class Remote(Side):
    name = "remote"

    def __init__(
        self,
        peer: str,
        password: str,
        remote_dir: str,
        iface: str | None,
        remote_log_dir: str = "/tmp",
    ):
        super().__init__(iface)
        self.peer = peer
        self.password = password
        self.remote_dir = os.path.expanduser(remote_dir)
        self.remote_log_dir = remote_log_dir
        self.base = [
            "sshpass",
            "-p",
            password,
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=10",
            peer,
        ]

    @property
    def python(self) -> str:
        return f"{self.remote_dir}/.venv/bin/python"

    def _sh(self, remote_cmd: str) -> str:
        return f"cd {shlex.quote(self.remote_dir)} && {remote_cmd}"

    def run_fg(self, cmd: str, timeout: int) -> tuple[int, str]:
        process = subprocess.run(
            [*self.base, self._sh(cmd)],
            capture_output=True,
            text=True,
            timeout=timeout + 15,
        )
        return process.returncode, process.stdout + process.stderr

    def run_bg(self, cmd: str, logname: str) -> None:
        path = f"{self.remote_log_dir}/{logname}"
        # The command may contain subshells/pipes (stdin feeders), so it must
        # go through an explicitly quoted `bash -c` — `nohup ( … )` directly
        # would be a bash syntax error and the remote process would never start.
        wrapped = f"(nohup bash -c {shlex.quote(cmd)} > {shlex.quote(path)} 2>&1 < /dev/null &); sleep 0.3; echo started"
        subprocess.run(
            [*self.base, self._sh(wrapped)], capture_output=True, text=True, timeout=30
        )

    def pkill(self, regex: str) -> None:
        subprocess.run(
            [*self.base, f"pkill -f -- {shlex.quote(regex)}"], check=False, timeout=20
        )

    def read_file(self, path: str) -> str:
        try:
            process = subprocess.run(
                [*self.base, f"cat {shlex.quote(path)} 2>/dev/null"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            return process.stdout
        except subprocess.TimeoutExpired:
            return ""

    def sha256(self, path: str) -> str:
        _rc, out = self.run_fg(f"sha256sum {shlex.quote(path)} 2>/dev/null", 20)
        return out.split()[0] if out.strip() else ""

    def write_bytes(self, path: str, size: int) -> str:
        data = os.urandom(size)
        tmp = "/tmp/xsmoke-payload.bin"
        Path(tmp).write_bytes(data)
        subprocess.run(
            [
                "sshpass",
                "-p",
                self.password,
                "scp",
                "-o",
                "StrictHostKeyChecking=no",
                tmp,
                f"{self.peer}:{path}",
            ],
            check=True,
            timeout=60,
        )
        Path(tmp).unlink()
        return hashlib.sha256(data).hexdigest()

    def kill_all(self) -> None:
        # Per-rid cleanup covers remote processes.
        pass


TESTS: list[dict] = []


def test(name: str, group: str):
    def deco(fn):
        TESTS.append({"name": name, "group": group, "fn": fn})
        return fn

    return deco


class Ctx:
    """Per-direction context handed to every test function."""

    def __init__(self, host: Local, vm: Remote, ports: dict[str, int]):
        self.host = host
        self.vm = vm
        self.ports = ports
        self.rid_counter = 0

    def rid(self, base: str, side: Side, role: str) -> str:
        self.rid_counter += 1
        return f"{base}-{side.name}-{role}-{self.rid_counter}"


def stdin_feeder(lines: list[tuple[float, str]], tail: float) -> str:
    """Shell snippet producing delayed stdin lines for interactive examples."""
    parts = []
    for delay, text in lines:
        parts.append(f"sleep {delay}; echo {shlex.quote(text)}; ")
    parts.append(f"sleep {tail}")
    return "( " + "".join(parts) + ") "


@test("presence", "lan")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["presence"]
    rid1, rid2 = ctx.rid("pres", first, "1"), ctx.rid("pres", second, "2")
    first.start_example("presence", ["alpha", "--port", str(port)], rid1, 60)
    time.sleep(3)
    second.start_example("presence", ["beta", "--port", str(port)], rid2, 40)
    ok1 = second.wait_log(second.log_path(rid2), "ENTER alpha", 25)
    ok2 = first.wait_log(first.log_path(rid1), "ENTER beta", 25)
    first.pkill_marker(rid1)
    second.pkill_marker(rid2)
    misses = [
        n
        for n, ok in zip(["second-sees-first", "first-sees-second"], [ok1, ok2])
        if not ok
    ]
    return (not misses), f"missing: {misses}" if misses else ""


@test("chat", "lan")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["chat"]
    rid1, rid2 = ctx.rid("chat", first, "1"), ctx.rid("chat", second, "2")
    uid1_hex = label_hex(f"{UUID_PREFIX}{rid1}")
    uid2_hex = label_hex(f"{UUID_PREFIX}{rid2}")
    lp, sp = first.log_path(rid1), second.log_path(rid2)
    cmd1 = (
        stdin_feeder(
            [
                (6.0, f"hello-from-{first.name}"),
                (3.0, f"/w {uid2_hex} psst-{first.name}"),
            ],
            8,
        )
        + "| "
        + first.example_cmd("chat", ["alpha", "--port", str(port)], rid1, 45)
        + f" > {shlex.quote(lp)} 2>&1"
    )
    cmd2 = (
        stdin_feeder(
            [
                (6.0, f"hello-from-{second.name}"),
                (3.0, f"/w {uid1_hex} psst-{second.name}"),
            ],
            8,
        )
        + "| "
        + second.example_cmd("chat", ["beta", "--port", str(port)], rid2, 45)
        + f" > {shlex.quote(sp)} 2>&1"
    )
    first.run_bg(cmd1, f"xsmoke-{rid1}.log")
    second.run_bg(cmd2, f"xsmoke-{rid2}.log")
    ok1 = second.wait_log(sp, f"alpha: hello-from-{first.name}", 25)
    ok2 = first.wait_log(lp, f"beta: hello-from-{second.name}", 25)
    ok3 = second.wait_log(sp, f"(private): psst-{first.name}", 10)
    ok4 = first.wait_log(lp, f"(private): psst-{second.name}", 10)
    time.sleep(1)
    first.pkill_marker(rid1)
    second.pkill_marker(rid2)
    misses = [
        n
        for n, ok in zip(
            ["shout1", "shout2", "whisper1", "whisper2"], [ok1, ok2, ok3, ok4]
        )
        if not ok
    ]
    return (not misses), f"missing: {misses}" if misses else ""


@test("secure_chat", "lan")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    key = hashlib.sha256(b"xsmoke-key").hexdigest()
    port = ctx.ports["secure_chat"]
    rid1, rid2 = ctx.rid("sc", first, "1"), ctx.rid("sc", second, "2")
    lp, sp = first.log_path(rid1), second.log_path(rid2)
    cmd1 = (
        stdin_feeder([(6.0, f"secret-from-{first.name}")], 5)
        + "| "
        + first.example_cmd(
            "secure_chat", ["alpha", "--port", str(port), "--shared-key", key], rid1, 40
        )
        + f" > {shlex.quote(lp)} 2>&1"
    )
    cmd2 = (
        stdin_feeder([(6.0, f"secret-from-{second.name}")], 5)
        + "| "
        + second.example_cmd(
            "secure_chat", ["beta", "--port", str(port), "--shared-key", key], rid2, 40
        )
        + f" > {shlex.quote(sp)} 2>&1"
    )
    first.run_bg(cmd1, f"xsmoke-{rid1}.log")
    second.run_bg(cmd2, f"xsmoke-{rid2}.log")
    ok1 = second.wait_log(sp, f"alpha: secret-from-{first.name}", 25)
    ok2 = first.wait_log(lp, f"beta: secret-from-{second.name}", 25)
    time.sleep(1)
    ok3 = "Decrypt failed" not in (first.read_file(lp) + second.read_file(sp))
    first.pkill_marker(rid1)
    second.pkill_marker(rid2)
    misses = [
        n
        for n, ok in zip(["a->b", "b->a", "no-decrypt-fail"], [ok1, ok2, ok3])
        if not ok
    ]
    return (not misses), f"missing: {misses}" if misses else ""


@test("whiteboard", "lan")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["whiteboard"]
    rid1, rid2 = ctx.rid("wb", first, "1"), ctx.rid("wb", second, "2")
    # the example names its node "board-<name>" internally
    first.start_example("whiteboard", ["a", "--port", str(port), "--demo"], rid1, 60)
    time.sleep(3)
    second.start_example("whiteboard", ["b", "--port", str(port), "--demo"], rid2, 40)
    ok1 = second.wait_log(second.log_path(rid2), "stroke from board-a:", 30)
    ok2 = first.wait_log(first.log_path(rid1), "stroke from board-b:", 25)
    first.pkill_marker(rid1)
    second.pkill_marker(rid2)
    misses = [n for n, ok in zip(["a->b", "b->a"], [ok1, ok2]) if not ok]
    return (not misses), f"missing: {misses}" if misses else ""


@test("fast_tick", "lan")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["fast_tick"]
    rid1, rid2 = ctx.rid("ft", first, "1"), ctx.rid("ft", second, "2")
    first.start_example(
        "fast_tick",
        ["--role", "registry", "--port", str(port), "--run-seconds", "12"],
        rid1,
        40,
    )
    time.sleep(2)
    second.start_example(
        "fast_tick",
        ["--role", "service", "--port", str(port), "--run-seconds", "10"],
        rid2,
        35,
    )
    ok1 = first.wait_log(first.log_path(rid1), "ENTER peer=", 25)
    ok2 = second.wait_log(second.log_path(rid2), "ENTER peer=", 25)
    first.pkill_marker(rid1)
    second.pkill_marker(rid2)
    misses = [n for n, ok in zip(["a", "b"], [ok1, ok2]) if not ok]
    return (not misses), f"missing: {misses}" if misses else ""


@test("sensor_network", "coord")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["sensor_network"]
    rid1, rid2 = ctx.rid("sens", first, "agg"), ctx.rid("sens", second, "sensors")
    first.start_example("sensor_network", ["aggregator", "--port", str(port)], rid1, 60)
    if not first.wait_log(first.log_path(rid1), "Aggregator started", 20):
        return False, "aggregator never started"
    second.start_example("sensor_network", ["sensors", "--port", str(port)], rid2, 40)
    ok1 = first.wait_log(first.log_path(rid1), "Reading from temp-001", 30)
    first.pkill_marker(rid1)
    second.pkill_marker(rid2)
    return ok1, "" if ok1 else "aggregator saw no readings"


@test("health_monitor", "coord")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["health_monitor"]
    rid1, rid2 = ctx.rid("hm", first, "1"), ctx.rid("hm", second, "2")
    first.start_example("health_monitor", ["mon-1", "--port", str(port)], rid1, 60)
    time.sleep(3)
    second.start_example("health_monitor", ["mon-2", "--port", str(port)], rid2, 40)
    ok1 = second.wait_log(second.log_path(rid2), "Peer joined: mon-1", 25)
    ok2 = first.wait_log(first.log_path(rid1), "Peer joined: mon-2", 25)
    first.pkill_marker(rid1)
    second.pkill_marker(rid2)
    misses = [n for n, ok in zip(["a", "b"], [ok1, ok2]) if not ok]
    return (not misses), f"missing: {misses}" if misses else ""


@test("task_queue", "coord")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["task_queue"]
    rid1, rid2 = ctx.rid("tq", first, "coord"), ctx.rid("tq", second, "worker")
    first.start_example("task_queue", ["coordinator", "--port", str(port)], rid1, 80)
    if not first.wait_log(first.log_path(rid1), "waiting for workers", 20):
        return False, "coordinator never started"
    # the example names its node "worker-<name>" internally
    second.start_example("task_queue", ["worker", "w1", "--port", str(port)], rid2, 50)
    ok1 = first.wait_log(first.log_path(rid1), "done by worker-w1", 45)
    ok2 = second.wait_log(second.log_path(rid2), "got task ", 45)
    first.pkill_marker(rid1)
    second.pkill_marker(rid2)
    misses = [n for n, ok in zip(["coord-result", "worker-task"], [ok1, ok2]) if not ok]
    return (not misses), f"missing: {misses}" if misses else ""


@test("service_discovery", "coord")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["service_discovery"]
    rid1 = ctx.rid("svc", first, "reg")
    rid2 = ctx.rid("svc", first, "svc")
    rid3 = ctx.rid("svc", second, "cli")
    first.start_example(
        "service_discovery", ["registry", "--port", str(port)], rid1, 70
    )
    time.sleep(1)
    first.start_example(
        "service_discovery",
        ["service", "my-svc", "api", "8080", "--port", str(port)],
        rid2,
        60,
    )
    time.sleep(3)
    second.start_example("service_discovery", ["client", "--port", str(port)], rid3, 45)
    ok1 = second.wait_log(
        second.log_path(rid3), "Service: my-svc (api) on port 8080", 30
    )
    ok2 = first.wait_log(first.log_path(rid1), "DISCOVERED: ", 25)
    first.pkill_marker(rid1)
    first.pkill_marker(rid2)
    second.pkill_marker(rid3)
    misses = [
        n for n, ok in zip(["client-found", "registry-saw"], [ok1, ok2]) if not ok
    ]
    return (not misses), f"missing: {misses}" if misses else ""


@test("config_sync", "coord")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["config_sync"]
    rid1, rid2 = ctx.rid("cfg", first, "writer"), ctx.rid("cfg", second, "watcher")
    # Writer FIRST, watcher joins after all sets are done: the only way the
    # late watcher can learn the values is the full-config rebroadcast the
    # manager fires when a new peer ENTERs.
    first.start_example("config_sync", ["node-1", "--port", str(port)], rid1, 70)
    time.sleep(6)
    second.start_example("config_sync", ["node-2", "--port", str(port)], rid2, 60)
    ok1 = second.wait_log(second.log_path(rid2), "Config updated: cache_ttl", 45)
    first.pkill_marker(rid1)
    second.pkill_marker(rid2)
    return ok1, "" if ok1 else "late watcher missed rebroadcast"


@test("distributed_lock", "coord")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["distributed_lock"]
    rid1, rid2 = ctx.rid("lk", first, "1"), ctx.rid("lk", second, "2")
    first.start_example("distributed_lock", ["n1", "--port", str(port)], rid1, 70)
    time.sleep(2)
    second.start_example("distributed_lock", ["n2", "--port", str(port)], rid2, 60)
    ok1 = first.wait_log(first.log_path(rid1), "Got lock!", 45)
    ok2 = second.wait_log(second.log_path(rid2), "Got lock!", 45)
    first.pkill_marker(rid1)
    second.pkill_marker(rid2)
    misses = [n for n, ok in zip(["a", "b"], [ok1, ok2]) if not ok]
    return (not misses), f"missing: {misses}" if misses else ""


@test("game_sync", "coord")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["game_sync"]
    rid1, rid2 = ctx.rid("gm", first, "srv"), ctx.rid("gm", second, "client")
    first.start_example("game_sync", ["server", "--port", str(port)], rid1, 60)
    time.sleep(3)
    second.start_example(
        "game_sync", ["client", "Alice", "--port", str(port)], rid2, 45
    )
    ok1 = first.wait_log(first.log_path(rid1), "player-Alice joined the world", 30)
    first.pkill_marker(rid1)
    second.pkill_marker(rid2)
    return ok1, "" if ok1 else "server saw no player"


@test("file_transfer", "data")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["file_transfer"]
    # Payload name is unique per direction (receiver side).
    tag = f"{first.name}"
    payload = f"/tmp/xsmoke-ft-{tag}.bin"
    out_dir = f"/tmp/xsmoke-ft-out-{tag}"
    size = 1_500_000
    rid_rx = ctx.rid("ftrx", first, "rx")
    rid_tx = ctx.rid("fttx", second, "tx")
    # The label is hashed to the peer hex by the example's parse_uuid.
    rx_uuid = f"{UUID_PREFIX}{rid_rx}"
    first.rm_rf(out_dir)
    first.start_example(
        "file_transfer", ["receive", out_dir, "--port", str(port)], rid_rx, 80
    )
    if not first.wait_log(first.log_path(rid_rx), "UUID:", 20):
        return False, "receiver never printed its UUID"
    src_sha = second.write_bytes(payload, size)
    second.start_example(
        "file_transfer",
        ["send", rx_uuid, payload, "--port", str(port)],
        rid_tx,
        60,
    )
    ok1 = first.wait_log(first.log_path(rid_rx), "Transfer complete", 60)
    time.sleep(2)
    got = first.sha256(f"{out_dir}/xsmoke-ft-{tag}.bin")
    first.pkill_marker(rid_rx)
    second.pkill_marker(rid_tx)
    if not ok1:
        return False, "no Transfer complete"
    if got != src_sha:
        return False, f"sha mismatch: got={got[:12]} want={src_sha[:12]}"
    return True, ""


@test("media_stream", "data")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["media_stream"]
    # Payload name is unique per direction (receiver side).
    tag = f"{first.name}"
    payload = f"/tmp/xsmoke-md-{tag}.mp4"
    out_dir = f"/tmp/xsmoke-md-out-{tag}"
    size = 1_200_000
    rid_rx = ctx.rid("mdrx", first, "rx")
    rid_tx = ctx.rid("mdtx", second, "tx")
    first.rm_rf(out_dir)
    first.start_example(
        "media_stream", ["recv", "--out", out_dir, "--port", str(port)], rid_rx, 80
    )
    if not first.wait_log(first.log_path(rid_rx), "waiting", 20):
        return False, "receiver never started"
    src_sha = second.write_bytes(payload, size)
    second.start_example(
        "media_stream", ["send", "--file", payload, "--port", str(port)], rid_tx, 60
    )
    ok1 = first.wait_log(first.log_path(rid_rx), "MEDIA_END", 60)
    time.sleep(2)
    got = first.sha256(f"{out_dir}/xsmoke-md-{tag}.mp4")
    first.pkill_marker(rid_rx)
    second.pkill_marker(rid_tx)
    if not ok1:
        return False, "no MEDIA_END"
    if got != src_sha:
        return False, f"sha mismatch: got={got[:12]} want={src_sha[:12]}"
    return True, ""


@test("wan_direct", "wan")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    # `first` runs the listener; `second` dials it directly via --peer.
    port = ctx.ports["wan_direct"]
    first_addr = ctx.ports.get("_first_addr", "")
    if not first_addr:
        return True, "skipped: pass --first-addr IP to enable"
    listen_port = ctx.ports["_wan_listen"]
    target = f"{first_addr}:{listen_port}"
    rid1, rid2 = ctx.rid("wan", first, "remote"), ctx.rid("wan", second, "local")
    msg = f"wan-{first.name}-to-{second.name}"
    first.start_example(
        "wan_direct",
        ["wan-remote", "--listen-port", str(listen_port), "--port", str(port)],
        rid1,
        70,
    )
    time.sleep(3)
    second.start_example(
        "wan_direct",
        [
            "wan-local",
            "--peer",
            target,
            "--send",
            msg,
            "--wait",
            "20",
            "--port",
            str(port),
        ],
        rid2,
        50,
    )
    ok1 = second.wait_log(second.log_path(rid2), "ENTER wan-remote", 35)
    ok2 = first.wait_log(first.log_path(rid1), f"SHOUT from wan-local: {msg}", 35)
    first.pkill_marker(rid1)
    second.pkill_marker(rid2)
    misses = [n for n, ok in zip(["dial", "shout"], [ok1, ok2]) if not ok]
    return (not misses), f"missing: {misses}" if misses else ""


PORT_ORDER = [
    "presence",
    "chat",
    "secure_chat",
    "whiteboard",
    "fast_tick",
    "sensor_network",
    "task_queue",
    "service_discovery",
    "config_sync",
    "health_monitor",
    "distributed_lock",
    "game_sync",
    "file_transfer",
    "media_stream",
    "wan_direct",
    "uuid_collision",
]


def port_probe(side: Side, port: int, tcp: bool) -> bool:
    flag = "-tlnp" if tcp else "-ulnp"
    _rc, out = side.run_fg(
        f"ss {flag} 2>/dev/null | grep -q ':{port} ' && echo BUSY || echo FREE", 15
    )
    return "FREE" in out


@test("uuid_collision", "lan")
def _(ctx: Ctx, base: str, first: Side, second: Side) -> tuple[bool, str]:
    port = ctx.ports["uuid_collision"]
    rid_a = ctx.rid("uc", first, "a")
    rid_b = ctx.rid("uc", first, "b")
    # Two nodes, SAME stable uuid (the second reuses the first's label).
    first.start_example("presence", ["coll-a", "--port", str(port)], rid_a, 30)
    time.sleep(3)
    collision_cmd = first.example_cmd(
        "presence", ["coll-b", "--port", str(port)], rid_a, 25
    )
    first.run_bg(collision_cmd, f"xsmoke-{rid_b}.log")
    # SO_REUSEPORT on the shared beacon port means the kernel's 4-tuple hash
    # may deliver all beacons to ONE of the two sockets — the guard can fire
    # in either node (or both), so accept the ERROR in either log.
    ok1 = first.wait_log(first.log_path(rid_b), "ERROR: another node with uuid", 20)
    ok1 = ok1 or first.wait_log(
        first.log_path(rid_a), "ERROR: another node with uuid", 8
    )
    first.pkill_marker(rid_a)
    first.pkill_marker(rid_b)
    return ok1, "" if ok1 else "collision guard did not fire"


def cleanup_all(host: Side, remote: Side) -> None:
    # Bracket trick: pattern never matches the shell executing it.
    pat = f"--uui[d] {UUID_PREFIX}"
    host.pkill(pat)
    remote.pkill(pat)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--peer", required=True, help="user@host for the remote side")
    ap.add_argument(
        "--password",
        default=os.environ.get("SSHPASS_PW", ""),
        help="ssh password (or SSHPASS_PW env)",
    )
    ap.add_argument(
        "--remote-dir", default="~/buffy-zre", help="checkout dir on the remote side"
    )
    ap.add_argument(
        "--host-iface", default=None, help="pin local beacons to this interface/IP"
    )
    ap.add_argument(
        "--remote-iface", default=None, help="pin remote beacons to this interface/IP"
    )
    ap.add_argument(
        "--base-port",
        type=int,
        default=24800,
        help="first UDP port (16 ports + 1 TCP at +200)",
    )
    ap.add_argument(
        "--first-addr",
        default="",
        help="IP of the LOCAL machine as seen by the remote (enables wan_direct)",
    )
    ap.add_argument(
        "--groups", default="all", help="all|lan|coord|data|wan (comma-separated ok)"
    )
    ap.add_argument(
        "-k",
        dest="filter_",
        default="",
        help="only tests whose name contains one of these comma-separated substrings",
    )
    ap.add_argument("--list-tests", action="store_true")
    ap.add_argument(
        "--keep-logs", action="store_true", help="keep per-test logs for inspection"
    )
    args = ap.parse_args()

    if args.list_tests:
        for t in TESTS:
            print(f"{t['name']:20} group={t['group']}")
        return 0

    if not args.password:
        log("ERROR: --password or SSHPASS_PW required")
        return 2

    host = Local(args.host_iface, LOG_DIR)
    remote = Remote(args.peer, args.password, args.remote_dir, args.remote_iface)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log("== preflight: connectivity")
    _rc, out = remote.run_fg("echo ok", 20)
    if "ok" not in out:
        log(f"FAIL: cannot reach {args.peer}: {out[:200]}")
        return 2
    _rc, out = remote.run_fg("test -x .venv/bin/python && echo venv-ok", 20)
    if "venv-ok" not in out:
        log(f"FAIL: {remote.remote_dir}/.venv/bin/python missing on remote")
        return 2

    log("== preflight: ports")
    busy = []
    for i, name in enumerate(PORT_ORDER):
        port = args.base_port + i * 10
        if not port_probe(host, port, tcp=False) or not port_probe(
            remote, port, tcp=False
        ):
            busy.append(f"{name}:{port}(udp)")
    wan_listen = args.base_port + 200
    if not port_probe(host, wan_listen, tcp=True) or not port_probe(
        remote, wan_listen, tcp=True
    ):
        busy.append(f"wan:{wan_listen}(tcp)")
    if busy:
        log(f"FAIL: ports busy on a side: {busy} — choose another --base-port")
        return 2
    ports = {name: args.base_port + i * 10 for i, name in enumerate(PORT_ORDER)}
    ports["_wan_listen"] = wan_listen
    ports["_first_addr"] = args.first_addr
    log(f"   using ports {args.base_port}..{wan_listen} on both sides")

    log("== preflight: kill leftovers from previous runs")
    cleanup_all(host, remote)
    time.sleep(1)

    groups = None if args.groups == "all" else set(args.groups.split(","))
    selected = [
        t
        for t in TESTS
        if (groups is None or t["group"] in groups)
        and (
            not args.filter_
            or any(s.strip() in t["name"] for s in args.filter_.split(","))
        )
    ]
    if not selected:
        log("FAIL: no tests selected")
        return 2

    results: list[tuple[str, bool, str]] = []
    try:
        for t in selected:
            name = t["name"]
            log(f"-- {name}: {host.name}-first")
            try:
                ok_a, detail_a = t["fn"](Ctx(host, remote, ports), name, host, remote)
            except Exception as exc:
                ok_a, detail_a = False, f"exception: {exc}"
            results.append((f"{name} [{host.name}-first]", ok_a, detail_a))
            log(f"   {'PASS' if ok_a else 'FAIL'} {detail_a}")
            time.sleep(2)
            log(f"-- {name}: {remote.name}-first")
            try:
                ok_b, detail_b = t["fn"](Ctx(host, remote, ports), name, remote, host)
            except Exception as exc:
                ok_b, detail_b = False, f"exception: {exc}"
            results.append((f"{name} [{remote.name}-first]", ok_b, detail_b))
            log(f"   {'PASS' if ok_b else 'FAIL'} {detail_b}")
            time.sleep(2)
    finally:
        cleanup_all(host, remote)
        host.kill_all()

    log("== summary")
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        log(
            f"  {'PASS' if ok else 'FAIL'}  {name}"
            + (f"  ({detail})" if detail else "")
        )
    log(f"== {passed}/{len(results)} passed")
    if not args.keep_logs:
        host.run_fg(f"rm -rf {shlex.quote(str(LOG_DIR))}", 15)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
