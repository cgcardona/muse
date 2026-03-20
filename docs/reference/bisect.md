# `muse bisect` — binary search for regressions

`muse bisect` is a **regression-hunting power tool** for humans and agents alike.  Given a commit where a bug first appears (bad) and a commit where it was not present (good), it performs a binary search through the history between them — cutting the search space in half at each step — until the exact commit that introduced the bug is isolated.

For a 1,000-commit range, `muse bisect` needs at most 10 steps.  For 1,000,000, it needs 20.

## Subcommands

| Command | Description |
|---------|-------------|
| `muse bisect start` | Begin a bisect session |
| `muse bisect bad [ref]` | Mark a commit as bad (bug present) |
| `muse bisect good [ref]` | Mark a commit as good (bug absent) |
| `muse bisect skip [ref]` | Skip a commit that cannot be tested |
| `muse bisect run <cmd>` | Automatically bisect using a shell command |
| `muse bisect log` | Show the bisect session log |
| `muse bisect reset` | End the session and clean up state |

## Manual workflow

```bash
# 1. Start the session with the bad and good bounds:
muse bisect start --bad HEAD --good v1.0.0

# 2. Muse suggests a midpoint:
# Next to test: a1b2c3d4ef56  (32 remaining, ~5 steps left)

# 3. Test that commit and report the result:
muse bisect good    # or: muse bisect bad

# 4. Repeat until the first bad commit is found:
# ✅ First bad commit found: deadbeef1234…
# Run 'muse bisect reset' to end the session.

# 5. Clean up:
muse bisect reset
```

## Automated workflow (`muse bisect run`)

The `run` subcommand fully automates the search.  The command you provide is run at each bisect step; the exit code determines the verdict:

| Exit code | Verdict |
|-----------|---------|
| `0` | good — bug not present |
| `125` | skip — commit untestable (e.g. build fails) |
| `1–124`, `126–255` | bad — bug present |

```bash
muse bisect start --bad HEAD --good v1.0.0
muse bisect run "pytest tests/test_regression.py -x -q"
```

Muse will automatically advance until the first bad commit is found.

## State file

The bisect session is stored at `.muse/BISECT_STATE.toml`:

```toml
bad_id = "deadbeef…"
good_ids = ["aabbccdd…"]
skipped_ids = []
remaining = ["a1b2c3…", "d4e5f6…", …]
log = ["deadbeef… bad 2026-03-19T14:22:01+00:00", …]
branch = "main"
```

The state file is rebuilt at every step so it survives interruptions.

## Multiple good commits

You can specify multiple `--good` bounds to narrow the search range from multiple known-good ancestors:

```bash
muse bisect start --bad HEAD --good v1.0.0 --good v1.1.0 --good v1.2.0
```

## Agent workflow

```bash
# Fully autonomous regression hunt:
muse bisect start --bad HEAD --good "$LAST_GREEN_CI_COMMIT"
muse bisect run "./ci/test.sh"
# Agent reads the result and files a bug report with the first-bad commit.
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | No bisect session active, or ref not found |

## Interaction with other commands

- `muse bisect` operates on **existing commits** in the store — it does not check out files automatically.  For file-level testing, use `muse bisect run` with a script that reads `state/` directly.
- After `muse bisect reset`, all bisect state is removed and the normal branch workflow resumes.
