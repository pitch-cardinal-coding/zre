# Contributing to zre

Thanks for your interest in contributing! This document covers the basics for
getting a development environment running and submitting changes.

## Development setup

```bash
# Create a virtualenv (any Python >= 3.10 works; tested on 3.14)
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode with dev tools
pip install -e .[dev]

# Or install the pinned runtime/dev stack
pip install -r requirements.txt
pip install -e .
```

## Running tests and lint

```bash
make test      # pytest (99 tests)
make lint      # ruff check zre tests examples
make format    # ruff format
make ci        # check-format + lint + test
```

Notes:

- Tests use real UDP beacons on loopback (`127.255.255.255` / `127.0.0.1`),
  so they work inside containers and CI without extra setup.
- Tests need the ability to bind UDP ports and localhost TCP sockets.
- The library imports `fcntl` at module load, so it targets POSIX (Linux,
  macOS). Windows is not supported.

## Pull requests

1. Fork the repo and create a topic branch from `main`.
2. Make your change. Add tests for anything behavioral — new protocol
   handling, timers, or peer state transitions especially.
3. Run `make ci` and make sure it passes.
4. Keep the wire format untouched: `zre` speaks RFC 36 on the wire. If your
   change would alter framing, sequencing, or beacon layout, open an issue
   first to discuss it.
5. Update `CHANGELOG.md` under an "Unreleased" heading.
6. Open the PR with a short description of what changed and why.

## Releasing (maintainers)

Releases are published by GitHub Actions (`.github/workflows/release.yml`)
using PyPI [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) —
no API tokens involved:

1. Register the trusted publisher for project `zre` (one-time per index;
   project Publishing page if the project exists, else the account-level
   pending publisher form):
   - PyPI: <https://pypi.org/manage/project/zre/publishing/>
   - TestPyPI: <https://test.pypi.org/manage/project/zre/publishing/>

   Owner `pitch-cardinal-coding`, repo `zre`, workflow `release.yml`,
   environment `pypi` (or `testpypi` on TestPyPI).
2. In a pull request, update `CHANGELOG.md` and bump `version` in
   `pyproject.toml` (and `zre/__init__.py`). Merge it once CI is green —
   direct pushes to `main` are blocked by branch protection.
3. Sync `main`, then tag and push just the tag:
   `git tag v0.2.0 && git push origin v0.2.0`. The tag push triggers the
   test → build → publish pipeline.
4. Approve the publish when the workflow pauses at the `pypi` environment
   gate (Actions → the run → *Review deployments* → *Approve and deploy*).
   Releases do not upload until a maintainer approves.

The workflow tests on Python 3.10/3.12/3.14, builds the sdist + wheel,
runs `twine check`, smoke-imports the wheel, then uploads to both
TestPyPI and PyPI. Every push/PR to `main` also runs the test + build
jobs without publishing.

## Reporting bugs

Open a [GitHub issue](https://github.com/pitch-cardinal-coding/zre/issues)
with your Python version, OS, and a minimal reproduction (one of the
`examples/` scripts usually works well). For security-sensitive reports, see
[SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under
the [MIT License](LICENSE).
