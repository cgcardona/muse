# `muse worktree` — multiple simultaneous branch checkouts

A *worktree* is a second (or third, or hundredth) checked-out working directory linked to the **same** `.muse/` object store.  Each worktree has its own branch and its own `state/` directory — multiple agents (or engineers) can work on different branches simultaneously with zero interference.

## Mental model

```
myproject/                    ← main worktree
  state/                      ← main branch files
  .muse/                      ← shared object store, commits, refs

myproject-feat-audio/         ← linked worktree
  state/                      ← feat/audio files (populated at creation)

myproject-hotfix-001/         ← another linked worktree
  state/                      ← hotfix/001 files
```

All worktrees share one `.muse/` store.  A commit made in `myproject-feat-audio/` is immediately visible (by commit ID) to the main worktree and all other linked worktrees.

## Subcommands

### `muse worktree add <name> <branch>`

Create a new linked worktree checked out at `<branch>`.

```bash
muse worktree add feat-audio feat/audio
muse worktree add hotfix-001 hotfix/001
```

The worktree directory is created as a sibling of the repository root, named `<repo>-<name>`.  Its `state/` is pre-populated from the branch's latest snapshot.

**Constraints:**
- `<name>` is validated like a branch name (no path separators, no control characters).
- `<branch>` must already exist.
- A worktree with the same `<name>` must not already exist.

### `muse worktree list`

List all worktrees (main + linked).

```
  name                     branch                          HEAD          path
──────────────────────────────────────────────────────────────────────────────────
* (main)                   main                            cccccccc0000  /Users/me/myproject
  feat-audio               feat/audio                      a1b2c3d4ef56  /Users/me/myproject-feat-audio
  hotfix-001               hotfix/001                      deadbeef0012  /Users/me/myproject-hotfix-001
```

The `*` marks the main worktree.

### `muse worktree remove <name>`

Remove a linked worktree and its `state/` directory.

```bash
muse worktree remove feat-audio
muse worktree remove feat-audio --force   # skip confirmation
```

The branch itself is **not** deleted — only the worktree directory and its metadata entry are removed.

### `muse worktree prune`

Remove metadata entries for worktrees whose directories no longer exist (e.g. manually deleted).

```bash
muse worktree prune
```

## Agent workflows

### Parallel agent tasks

```bash
# Each agent works in its own worktree on its own branch:
muse worktree add agent-001 feat/agent-001
muse worktree add agent-002 feat/agent-002
muse worktree add agent-003 feat/agent-003

# Agents run independently in:
#   myproject-agent-001/state/
#   myproject-agent-002/state/
#   myproject-agent-003/state/
```

### Simultaneous hotfix + feature

```bash
# Keep working on the feature in the main worktree
# while fixing the hotfix in a linked worktree:
muse worktree add hotfix hotfix/critical-bug
cd ../myproject-hotfix
muse commit -m "fix: patch the critical bug"
muse push
cd ../myproject
# Continue on main branch, uninterrupted
```

## Worktree HEAD tracking

Each linked worktree has its own HEAD file stored at `.muse/worktrees/<name>.HEAD`.  This is independent of the main worktree's `.muse/HEAD`, allowing each worktree to be on a different branch simultaneously.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Validation error (invalid name, branch not found, etc.) |
