# `muse archive` — export a snapshot as a portable archive

`muse archive` packages any historical snapshot into a self-contained `tar.gz` or `zip` file.  The archive contains only the tracked files — no `.muse/` metadata is included.  This makes it the canonical format for distributing a specific version of your work.

## Usage

```bash
muse archive                                # HEAD snapshot → <sha12>.tar.gz
muse archive --ref v1.0.0                   # tag tip → <sha12>.tar.gz
muse archive --ref feat/audio               # branch tip
muse archive --ref a1b2c3d4                 # specific commit SHA prefix
muse archive --format zip --output out.zip  # zip format, custom name
muse archive --prefix myproject/            # add directory prefix inside archive
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--ref`, `-r` | HEAD | Branch name, tag, or commit SHA to archive |
| `--format`, `-f` | `tar.gz` | Archive format: `tar.gz` or `zip` |
| `--output`, `-o` | `<sha12>.<format>` | Output file path |
| `--prefix` | (none) | Directory prefix prepended to all paths inside the archive |

## Supported formats

| Format | Extension | Notes |
|--------|-----------|-------|
| `tar.gz` | `.tar.gz` | Compressed tar, widely supported |
| `zip` | `.zip` | ZIP with DEFLATE compression, Windows-friendly |

## Output

```
✅ Archive: release-v1.0.tar.gz  (47 file(s), 312.8 KiB)
   Commit:  a1b2c3d4ef56  feat: release v1.0
```

## What is included

- All files tracked in the snapshot manifest at the specified ref.
- Files are stored under their original relative paths (or under `--prefix/` if specified).

## What is NOT included

- `.muse/` metadata (commits, snapshots, object store)
- Untracked files from `state/`
- Reflog entries, branch refs, config

## `--prefix` usage

The prefix flag lets you distribute archives that unpack into a named directory, matching the convention of most open-source releases:

```bash
muse archive --prefix myproject-1.0/ --output myproject-1.0.tar.gz
# Inside the archive: myproject-1.0/README.md, myproject-1.0/src/main.py, …
```

## Agent workflows

### Create a release artifact

```bash
muse tag v1.0.0 --message "Release 1.0"
muse archive --ref v1.0.0 --format zip --output release-v1.0.zip
```

### Batch-archive all tags

```bash
for tag in $(muse tag list --names-only); do
    muse archive --ref "$tag" --output "archives/$tag.tar.gz"
done
```

### Distribute a specific commit

```bash
# Share an exact commit without exposing history:
muse archive --ref a1b2c3d4 --prefix shared-experiment/ --output experiment.zip
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Ref not found, unknown format, or snapshot missing |
