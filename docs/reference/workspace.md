# `muse workspace` — multi-repository workspaces

A *workspace* links multiple independent Muse repositories together under a single manifest, giving you a unified status view, one-shot synchronisation, and a clear model for multi-repo projects.

## When to use workspaces vs. worktrees

| Need | Use |
|------|-----|
| Multiple branches of **one** repo simultaneously | `muse worktree` |
| Multiple **separate** repos that evolve together | `muse workspace` |

## Use cases

- **A film score** with a melody repo, a harmony repo, and a samples repo
- **A machine learning pipeline** with a model repo, a dataset repo, and an eval repo
- **A micro-service backend** where each service lives in its own Muse repo
- **An agent swarm** where each autonomous agent manages its own state repo, and the coordinator workspace tracks them all

## Workspace manifest

The manifest lives at `.muse/workspace.toml`:

```toml
[[members]]
name   = "core"
url    = "https://musehub.ai/acme/core"
path   = "repos/core"
branch = "main"

[[members]]
name   = "sounds"
url    = "https://musehub.ai/acme/sounds"
path   = "repos/sounds"
branch = "v2"
```

## Subcommands

### `muse workspace add <name> <url>`

Register a member repository.

```bash
muse workspace add core   https://musehub.ai/acme/core
muse workspace add sounds https://musehub.ai/acme/sounds --branch v2
muse workspace add data   /path/to/local/dataset         --path vendor/data
```

| Option | Default | Description |
|--------|---------|-------------|
| `--path` | `repos/<name>` | Relative checkout path inside the workspace |
| `--branch`, `-b` | `main` | Branch to track |

Registration writes the manifest entry.  Run `muse workspace sync` to clone.

### `muse workspace list`

List all registered members with their status.

```
name                 branch           present  HEAD          url
──────────────────────────────────────────────────────────────────────────────
core                 main             yes      a1b2c3d4ef56  https://musehub.ai/acme/core
sounds               v2               yes      deadbeef0012  https://musehub.ai/acme/sounds
data                 main             no       (not cloned)  /path/to/local/dataset
```

### `muse workspace remove <name>`

Remove a member from the manifest.  The member's directory is **not** deleted.

```bash
muse workspace remove sounds
```

### `muse workspace status`

Rich status view for all members.

```
Workspace: /Users/me/myproject

✅  core                 branch=main  head=a1b2c3d4ef56
     path: /Users/me/myproject/repos/core
     url:  https://musehub.ai/acme/core

❌  data                 branch=main  head=not cloned
     path: /Users/me/myproject/repos/data
     url:  /path/to/local/dataset
```

### `muse workspace sync [name]`

Clone or pull all members (or one named member).

```bash
muse workspace sync         # sync everything
muse workspace sync core    # sync only 'core'
```

- If a member directory does not exist, `muse clone` is run to fetch it.
- If a member directory already exists, `muse pull` is run to update it.

## Agent workflows

### Coordinator + worker pattern

```bash
# Coordinator workspace tracks N worker repos:
muse workspace add worker-001 https://musehub.ai/swarm/worker-001
muse workspace add worker-002 https://musehub.ai/swarm/worker-002
muse workspace add worker-003 https://musehub.ai/swarm/worker-003

# Sync all workers to latest:
muse workspace sync

# Status across all:
muse workspace status
```

### Release pipeline

```bash
# Pin all members to release branches before archiving:
muse workspace add core    https://musehub.ai/acme/core    --branch release/v2
muse workspace add sounds  https://musehub.ai/acme/sounds  --branch release/v2
muse workspace sync

# Archive each member:
for member in repos/*/; do
    (cd "$member" && muse archive --output "../../archives/$(basename $member).tar.gz")
done
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Validation error, duplicate name, or member not found |
