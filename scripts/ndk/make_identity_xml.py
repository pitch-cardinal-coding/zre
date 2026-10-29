#!/usr/bin/env python3
"""Make the Android identity XML the dashboard/settings paste flow uses.

The app stores its Curve identity in SharedPreferences named
`zre_identity` with the two string keys `curve_pub` and `curve_sec`
(40-char Z85 each). On an emulator, scripts/ndk/zre_feed.sh plants this
XML those prefs so a validation run behaves exactly like a user
pasting the pair into the app's Settings sheet — the app's own code
path, no special switches.

The file is standard Android SharedPreferences XML; the app never has
to parse anything else. The security-sensitive crypto validation (does
the public key derive from the private key?) lives in curve_keys.py
(`make curve-check`) — run it before staging.

Usage:
  python3 make_identity_xml.py --name phone1 [--dir ~/.avedge] \\
      --out /tmp/zre_identity_feed.xml
"""

import argparse
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from zmq.utils import z85

PUB_KEY = "curve_pub"
SEC_KEY = "curve_sec"


def read_z85(path: Path) -> str:
    text = path.read_text().strip()
    if len(text) != 40:
        raise SystemExit(f"{path}: expected 40-char Z85, got {len(text)} chars")
    try:
        z85.decode(text)  # ValueError on non-Z85 characters
    except ValueError as exc:
        raise SystemExit(f"{path}: not valid Z85 ({exc})") from exc
    return text


def build_xml(pairs: dict[str, str]) -> str:
    lines = [
        "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>",
        "<map>",
    ]
    for name, value in pairs.items():
        # Z85 includes &, < and > — XML-escape or SharedPreferences
        # silently fails to parse the file (keys then look "missing").
        lines.append(f'    <string name="{name}">{escape(value)}</string>')
    lines.append("</map>")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True, help="identity name, e.g. phone1")
    parser.add_argument(
        "--dir", default=str(Path.home() / ".avedge"), help="key directory"
    )
    parser.add_argument("--out", required=True, help="XML file to write")
    args = parser.parse_args()

    key_dir = Path(args.dir).expanduser()
    pub = read_z85(key_dir / f"{args.name}.pub")
    sec = read_z85(key_dir / f"{args.name}.priv")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_xml({PUB_KEY: pub, SEC_KEY: sec}))
    out.chmod(0o600)
    print(f"{out} written: name={args.name} keys={PUB_KEY},{SEC_KEY}")


if __name__ == "__main__":
    sys.exit(main())
