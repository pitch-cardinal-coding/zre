# Releasing a new version (do-it-yourself guide)

This guide takes you from "code is ready" to "new version is live" with
exact commands and clicks. No step assumes prior release experience.

## The four things you are handling

| Thing | What it is, plainly | Where it lives |
|-------|---------------------|----------------|
| Version number | Three numbers like `0.2.0` in ONE file | `zre/__init__.py` (`pyproject.toml` reads it dynamically) |
| Tag | A sticky label (like `v0.2.0`) on one commit | GitHub, created by you |
| GitHub Release | The nice public page for the version | Repo → Releases |
| PyPI packages | What users download with `pip install zre` | `pypi.org/project/zre` |

Pushing the tag is the trigger: it starts a robot (GitHub Actions) that
tests, builds, and uploads. You approve the uploads by clicking.

## Version numbers: which one is next?

- New features → middle number up: `0.2.0` becomes `0.3.0`.
- Bug fixes only → last number up: `0.2.0` becomes `0.2.1`.
- The tag always has a `v` in front (`v0.3.0`); the files never do.

## Before you start (checklist)

- The pull request shows all green ticks (tests + build).
- `__version__` in `zre/__init__.py` shows the new number (no `v`).
  `pyproject.toml` has `dynamic = ["version"]` and takes the value from
  there, so there is nothing else to keep in sync.
- `docs/CHANGELOG.md` has a section for the new number with a date,
  plus the link at the bottom, e.g.
  `[0.3.0]: https://github.com/pitch-cardinal-coding/zre/releases/tag/v0.3.0`.

## Step 1 — Merge the pull request

Open the PR page and press **Merge pull request** (this repo only allows
squash merges, so pick **Squash and merge** if asked), then **Confirm**.

If GitHub refuses with "base branch policy prohibits the merge", the
branch protection list is stale: repo **Settings → Branches → main →
Edit**, and under *Status checks*, delete any check that no longer exists
(e.g. an old Python version) and tick the current ones
(`test py3.10`, `test py3.12`, `test py3.14`, `build + verify dists`).

## Step 2 — Tag the release on your machine

```bash
git checkout main && git pull
git tag v0.3.0 && git push origin v0.3.0
```

Replace `0.3.0` with your number. The second command wakes up the
release robot — check it started at **Actions** (the run named after
your tag).

The workflow refuses a tag whose commit is **not on `main`**, so always
sync `main` first. It also refuses a tag that does not match the built
artifact version, and (for PyPI) a version that already exists.

### Pre-releases

Use a PEP 440 normalized pre-release version in `__version__` and the
tag: `__version__ = "0.3.0rc1"` → tag `v0.3.0rc1` (no hyphen). A
pre-release tag publishes to **TestPyPI only**; PyPI is reserved for
stable `vX.Y.Z` tags. A tag like `v0.3.0-rc1` fails the artifact-name
check because the wheel is normalized to `0.3.0rc1`.

## Step 3 — Approve the uploads (two clicks, twice)

1. Open the tag's run on the **Actions** page.
2. A yellow banner appears: press **Review deployments**.
3. Tick **testpypi** first → **Approve and deploy**. Wait about a minute,
   then open `https://test.pypi.org/project/zre/` and confirm your
   number is there.
4. Back on the same run page, press **Review deployments** again → tick
   **pypi** → **Approve and deploy**. Confirm on
   `https://pypi.org/project/zre/`.
   For a pre-release tag there is no `pypi` deployment to approve —
   TestPyPI is the whole release.

Always approve TestPyPI first: it is the rehearsal. If it looks wrong,
stop there and fix forward instead of shipping it to real PyPI.

## Step 4 — Create the GitHub Release page

Pushing a tag does **not** make the Releases page entry — create it:

```bash
gh release create v0.3.0 --title "v0.3.0" --notes-file /tmp/relnotes.md
```

where `/tmp/relnotes.md` holds the new version's section copied out of
`docs/CHANGELOG.md`. Check
`https://github.com/pitch-cardinal-coding/zre/releases` afterwards.

## What is in the PyPI package

The sdist contains the `zre` package, the tests, and the project docs
(`docs/LICENSE`, `docs/CHANGELOG.md`, ...). The Android/NDK tooling
under `scripts/ndk/` and the C interop harness under `interop/` are
**repository material, not PyPI content** — they are not shipped in
the sdist.

## One-time setup (only ever needed once)

The robot uploads with no passwords: PyPI trusts GitHub directly. If a
publish job fails talking about OIDC or permissions, the trust link is
missing — add it once per index:

- PyPI: project page → **Publishing** → owner `pitch-cardinal-coding`,
  repo `zre`, workflow `release.yml`, environment `pypi`.
- TestPyPI: same, environment `testpypi`.

## If something goes wrong

| Symptom | Fix |
|---------|-----|
| Merge button blocked, checks are green | Stale check in branch protection (see Step 1). |
| Tag run fails on tests | Do not ship: fix on a branch, merge, delete the tag (`git push origin :v0.3.0`), re-tag after. |
| Publish job fails on login/OIDC | One-time setup above is missing. |
| Wrong version uploaded | You cannot overwrite PyPI: bump to the next number and repeat. |
| CI hangs with no output | Cancel the run; pytest now dumps a traceback after 3 minutes (`faulthandler_timeout` in `release.yml`) — read the last lines. |
