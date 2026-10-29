#!/usr/bin/env python3
"""CURVE key material for the Android (libzyre) side — one command.

The app consumes exactly two files per identity, in avedge's key-file
contract (bare 40-char Z85, one per file): `<name>.priv` (0600) and
`<name>.pub`. This tool generates/validates/stages that pair:

  ensure     create the pair ONLY if missing (never overwrites);
             delegates to the avedge toolchain when ~/devcode/avedge
             exists, so both projects produce byte-compatible files
  check      validate format AND that the public key derives from the
             private key (x25519, like avedge's `zcurve check`)
  release    copy a validated pair into a staging dir for the app
             (`dist/curve/<name>.priv|.pub` + a loading README)

Curve is not optional on the device: zc_peer and the app always run
Curve (this matches the project decision in libzyre-interop docs), so
both files MUST exist and be valid — there is no plain fallback.

Usage:
  python3 curve_keys.py ensure  --name phone1 [--dir ~/.avedge]
  python3 curve_keys.py check   --name phone1 [--dir ~/.avedge]
  python3 curve_keys.py release --name phone1 [--out ../dist/curve]
"""

import argparse
import subprocess
import sys
from pathlib import Path

import zmq
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
)
from zmq.utils import z85

AVEDGE_REPO = Path.home() / "devcode" / "avedge"
# The avedge venv lives under the project dir on this setup; probe the
# common locations so delegation fires wherever the operator has it.
AVEDGE_PY_CANDIDATES = (
    Path.home() / "devcode" / ".env" / "avedge" / "bin" / "python3",
    Path.home() / ".env" / "avedge" / "bin" / "python3",
)
AVEDGE_PY = next((p for p in AVEDGE_PY_CANDIDATES if p.exists()), None)


def z85_file(path: Path) -> str:
    text = path.read_text().strip()
    if len(text) != 40:
        raise ValueError(f"{path}: expected 40-char Z85, got {len(text)} chars")
    z85.decode(text)  # raises ValueError on non-Z85 characters
    return text


def write_z85_file(path: Path, text: str, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")
    if private:
        path.chmod(0o600)


def derive_public(secret_z85: str) -> str:
    raw = z85.decode(secret_z85)
    key = X25519PrivateKey.from_private_bytes(raw)
    raw_pub = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return z85.encode(raw_pub).decode("ascii")


def ensure_pair(name: str, key_dir: Path) -> tuple[str, str]:
    """Create the pair only if missing; never overwrite existing files."""
    priv, pub = key_dir / f"{name}.priv", key_dir / f"{name}.pub"
    if priv.exists() and pub.exists():
        return z85_file(pub), z85_file(priv)
    if priv.exists() or pub.exists():
        missing = priv if not priv.exists() else pub
        raise RuntimeError(f"half a pair exists; refusing: {missing}")
    if AVEDGE_REPO.exists() and AVEDGE_PY.exists():
        # Delegation keeps the two projects byte-identical and lets the
        # operator keep using make -C ~/devcode/avedge keys keys-check.
        subprocess.run(
            [str(AVEDGE_PY), "-m", "src.zmqkit.zcurve", "ensure", "--name", name],
            cwd=AVEDGE_REPO,
            env={
                "PATH": "/usr/bin:/bin",
                "AVEDGE_KEY_DIR": str(key_dir),
                "HOME": str(Path.home()),
            },
            check=True,
        )
        return z85_file(pub), z85_file(priv)
    pub_z85, sec_z85 = zmq.curve_keypair()
    write_z85_file(priv, sec_z85.decode(), private=True)
    write_z85_file(pub, pub_z85.decode(), private=False)
    return pub_z85.decode(), sec_z85.decode()


def check_pair(name: str, key_dir: Path) -> None:
    pub, sec = z85_file(key_dir / f"{name}.pub"), z85_file(key_dir / f"{name}.priv")
    derived = derive_public(sec)
    if derived != pub:
        raise ValueError(f"{key_dir}/{name}: public != x25519(secret)")
    print(f"OK {key_dir}/{name}: public={pub[:10]} (derives from private)")


def release_pair(name: str, key_dir: Path, out: Path) -> None:
    check_pair(name, key_dir)
    out.mkdir(parents=True, exist_ok=True)
    priv, pub = key_dir / f"{name}.priv", key_dir / f"{name}.pub"
    (out / f"{name}.priv").write_bytes(priv.read_bytes())
    (out / f"{name}.priv").chmod(0o600)
    (out / f"{name}.pub").write_bytes(pub.read_bytes())
    (out / f"{name}.pub").chmod(0o644)
    readme = out / "README.txt"
    readme.write_text(
        f"Curve key files for the Android app ({name})\n"
        f"============================================\n"
        f"{name}.priv  the secret key (bare 40-char Z85, one line, 0600)\n"
        f"{name}.pub   the public key (bare 40-char Z85, one line)\n"
        f"\nBoth are consumed by ZreNative.create(name, pubZ85, secZ85)\n"
        f"— Bundle them as assets or fetch them from the operator\n"
        f"(never commit the .priv to a public repository; rotate by\n"
        f"regenerating the pair and re-granting its .pub on the PC).\n"
    )
    print(f"staged: {out}/{{{name}.priv,{name}.pub}} + README.txt")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("ensure", "check", "release"))
    parser.add_argument("--name", default="phone1", help="pair name")
    parser.add_argument("--dir", default="~/.avedge", help="key directory")
    parser.add_argument("--out", default=None, help="release staging dir")
    args = parser.parse_args()
    key_dir = Path(args.dir).expanduser()
    if args.action == "ensure":
        ensure_pair(args.name, key_dir)
        check_pair(args.name, key_dir)
    elif args.action == "check":
        check_pair(args.name, key_dir)
    else:
        release_pair(args.name, key_dir, Path(args.out or "dist/curve"))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
