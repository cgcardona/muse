# `muse blame` — line-level attribution for text files

`muse blame` shows which commit last modified each line of a text file tracked in `state/`.  It is the universal, domain-agnostic attribution tool — the core VCS equivalent of `git blame`.

For domain-specific attribution, see:
- `muse midi note-blame` — per-bar attribution in MIDI tracks
- `muse code blame` — per-symbol attribution in code files

## Usage

```bash
muse blame README.md
muse blame --ref v1.0.0 docs/design.md
muse blame --porcelain state/config.toml | jq '.commit_id'
muse blame --short 8 src/main.py
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--ref`, `-r` | HEAD | Commit or branch to blame from |
| `--porcelain`, `-p` | false | Emit JSON objects instead of human-readable output |
| `--short` | 12 | Length of commit SHA prefix to display |

## Output (human-readable)

```
cccccccc0000  (Test User       2026-03-19)   1  hello world
```

Columns:
- Commit SHA prefix (12 chars by default)
- Author name (padded)
- Date (`YYYY-MM-DD`)
- Line number (right-aligned)
- Line content

## Output (`--porcelain`)

One JSON object per line:

```json
{"lineno": 1, "commit_id": "cccccccc…", "author": "Test User", "committed_at": "2026-03-19T14:22:01+00:00", "message": "initial commit", "content": "hello world"}
```

## How attribution works

The blame algorithm walks the commit graph backwards from the requested ref:

1. Every line in the file at HEAD is initially attributed to HEAD.
2. For each ancestor commit, a unified diff is computed between the ancestor's version and its child's.
3. Lines that appear unchanged in both versions are **re-attributed to the ancestor** — the older commit that first introduced them.
4. Lines that were added by a commit remain attributed to that commit.

This is equivalent to Git's blame algorithm for linear histories.  For merge commits, the algorithm chooses the first parent to keep attribution clean.

## Agent workflows

### Find who introduced a bug

```bash
muse blame --porcelain src/parser.py \
  | jq 'select(.content | test("legacy_mode")) | .commit_id' \
  | sort -u
```

### Audit a configuration file

```bash
muse blame config/production.toml
```

### Annotate every line with its commit

```bash
muse blame --porcelain README.md > blame-report.jsonl
```

## Limitations

- Only text files are supported.  Binary files (images, MIDI, etc.) should use domain-specific blame commands.
- The walk follows the first-parent chain for merge commits.  To attribute across both parents, use `muse midi note-blame` or `muse code blame` which are domain-aware.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | File not found at the specified ref, or ref not found |
