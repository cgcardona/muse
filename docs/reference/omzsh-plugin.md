# Muse Oh My ZSH Plugin — Full Reference

This document is the complete reference for the Muse Oh My ZSH plugin. For a quick-start guide see [`tools/omzsh-plugin/README.md`](../../tools/omzsh-plugin/README.md).

---

## Architecture

The plugin is divided into 14 sections (§0–§13) inside `tools/omzsh-plugin/muse.plugin.zsh`. The companion file `tools/omzsh-plugin/_muse` contains the ZSH completion function.

### Performance model

The prompt segment must never block the shell. The plugin achieves this by:

1. **Zero-subprocess file reads** for branch (`.muse/HEAD`), merge state (`.muse/MERGE_STATE.json`), and domain (`.muse/repo.json`) — all raw file reads inside ZSH.
2. **One python3 subprocess** per full refresh for JSON/TOML parsing (domain, user type, merge state, SemVer). This is batched into a single call.
3. **One muse subprocess** for dirty detection (`muse status --porcelain`), guarded by `$MUSE_DIRTY_TIMEOUT`. Only runs after a muse command (`$_MUSE_CMD_RAN=1`) or on a cold cache.
4. **Cached state** in `$MUSE_*` env vars, invalidated by `chpwd` and `preexec`.

```
chpwd ─────────────────────────────────► invalidate → _muse_refresh_fast
                                                         (head + meta only)
preexec (muse cmd) ─► _MUSE_CMD_RAN=1
precmd ─────────────────────────────────► _MUSE_CMD_RAN? → _muse_refresh
                                                              (full: + dirty + semver)
                      _MUSE_CACHE_VALID=0? → _muse_refresh
```

### Environment variables

All `$MUSE_*` variables are exported and visible to subprocesses. This is the machine-to-machine interface — an orchestrating AI agent can read any of these.

| Variable | Type | Content |
|---|---|---|
| `MUSE_REPO_ROOT` | string | Absolute path to the repo root, or `""` |
| `MUSE_DOMAIN` | string | Active domain plugin (`midi`, `code`, `bitcoin`, …) |
| `MUSE_BRANCH` | string | Current branch name, or 8-char SHA if detached |
| `MUSE_DETACHED` | 0/1 | 1 when HEAD is detached |
| `MUSE_DIRTY` | 0/1 | 1 when working tree has uncommitted changes |
| `MUSE_DIRTY_COUNT` | integer | Number of changed paths |
| `MUSE_DIRTY_STATE` | string | `"clean"` \| `"dirty"` \| `"?"` (timeout) |
| `MUSE_MERGING` | 0/1 | 1 when `MERGE_STATE.json` exists |
| `MUSE_MERGE_BRANCH` | string | The branch being merged in |
| `MUSE_CONFLICT_COUNT` | integer | Number of conflict paths |
| `MUSE_USER_TYPE` | string | `"human"` \| `"agent"` (from `config.toml`) |
| `MUSE_LAST_SEMVER` | string | `"major"` \| `"minor"` \| `"patch"` \| `""` |
| `MUSE_CONTEXT_JSON` | JSON string | Full compact context (see below) |
| `MUSE_SESSION_MODEL_ID` | string | Active agent session model |
| `MUSE_SESSION_AGENT_ID` | string | Active agent session ID |
| `MUSE_SESSION_START` | ISO-8601 | When the agent session started |
| `MUSE_SESSION_LOG_FILE` | path | Path to the active session `.jsonl` log |

---

## Prompt Functions

### `muse_prompt_info()`

Primary left-prompt segment. Outputs nothing if not in a muse repo.

**Human mode** (default):
```
♪ midi:main ✓
♪ midi:feat/x ✗ 3Δ
♪ midi:main ⚡ ← feature/exp (2 conflicts)
♪ midi:main ✓ [🤖 claude-4.6-sonnet]
```

**Agent mode** (`MUSE_AGENT_MODE=1`):
```
[midi|main|clean|no-merge]
[midi|main|dirty:3|merging:feature/exp:2|agent:coding-assistant]
```

### `muse_rprompt_info()`

Right-prompt SemVer indicator. Shows the `sem_ver_bump` field of the HEAD commit.

```
[MINOR]   (yellow)
[MAJOR]   (red)
[PATCH]   (green)
```

### `prompt_muse_vcs()` and `instant_prompt_muse_vcs()`

Powerlevel10k segment implementations. Use by adding `muse_vcs` to
`POWERLEVEL9K_LEFT_PROMPT_ELEMENTS` or `POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS`.

---

## Workflow Functions

### Branch creation

```zsh
muse-new-feat <name>      # creates and switches to feat/<name>
muse-new-fix <name>       # creates and switches to fix/<name>
muse-new-refactor <name>  # creates and switches to refactor/<name>
```

### Commits

```zsh
muse-wip
```
Commits with message `[WIP] 2026-03-20T14:30:00Z`. Useful for checkpointing
before switching context.

```zsh
muse-quick-commit
```
Interactive guided commit. Prompts for message and domain-specific metadata:
- **midi**: section (verse/chorus/bridge), track name, emotion
- **code**: module/package, breaking change flag

```zsh
muse-agent-commit "message" [extra flags…]
```
Wraps `muse commit` and auto-injects `--agent-id` and `--model-id` from the
active agent session (`$MUSE_SESSION_AGENT_ID`, `$MUSE_SESSION_MODEL_ID`).
Use this instead of bare `muse commit` when inside an agent session.

### Sync & merge

```zsh
muse-sync
```
Runs `muse fetch && muse pull && muse status` in sequence.

```zsh
muse-safe-merge <branch>
```
Runs `muse merge`. If conflicts occur, prints a structured conflict list and
optionally opens conflict paths in `$EDITOR`.

### Health & provenance

```zsh
muse-health
```
Shows a formatted health summary: dirty state, merge state, stash count, configured remotes, domain, user type, active agent session.

```zsh
muse-who-last
```
Shows authorship provenance of the HEAD commit. Distinguishes human vs agent authorship and shows model ID if present.

```zsh
muse-agent-blame [N]
```
Scans the last N commits (default 10) and prints a provenance table:

```
  Agent provenance — last 10 commits on main
  SHA        Date        Type    Author/Model                    Message
  ────────── ──────────  ──────  ──────────────────────────────  ──────────────
  a1b2c3d4   2026-03-20  agent   claude-4.6-sonnet               Refactor notes
  e5f6a7b8   2026-03-19  human   gabriel                         Add bass track
```

```zsh
muse-overview
```
Domain-specific live state overview. Calls `muse midi notes` for MIDI repos,
`muse code symbols` for code repos.

---

## Agent-Native Functions

### `muse-context [--json|--toml|--oneline]`

Outputs a compact, token-efficient repo context block. Designed to be injected
into an AI agent's context window.

**Default (human)**:
```
  MUSE REPO CONTEXT  ♪ midi:main
  ──────────────────────────────────────────────────────
  domain      midi
  branch      main
  commit      a1b2c3d4  "Add verse melody"  (2026-03-20)
  last author gabriel [human]
  semver      MINOR
  dirty       yes — 3 changed
  merging     no
  remotes     origin
  user        human
```

**`--json`**: pretty-printed JSON (uses `$MUSE_CONTEXT_JSON` as source)

**`--toml`**: TOML format (two sections: `[repo]` and `[commit]`)

**`--oneline`**: `midi:main dirty:3 commit:a1b2c3d4`

### `muse-agent-session <model_id> [agent_id]`

Begins a named agent session:
- Exports `$MUSE_SESSION_MODEL_ID`, `$MUSE_SESSION_AGENT_ID`, `$MUSE_SESSION_START`
- Creates a `.jsonl` session log in `$MUSE_SESSION_LOG_DIR`
- Exports `$MUSE_SESSION_LOG_FILE` pointing to the log
- Updates the prompt to show `[🤖 <model_id>]`

```zsh
muse-agent-session claude-4.6-sonnet coding-assistant
# model    claude-4.6-sonnet
# agent    coding-assistant
# log      ~/.muse/sessions/20260320-143000-12345.jsonl
```

### `muse-agent-end`

Ends the current session, writes a `session_end` entry to the log, unsets all
`$MUSE_SESSION_*` variables, and refreshes the prompt.

### `muse-sessions [file]`

Without arguments: lists the 20 most recent session `.jsonl` files with model ID, agent ID, and start time.

With a file argument: replays the session log, showing each command with its timestamp, exit code, and elapsed milliseconds:

```
2026-03-20T14:23:11  SESSION START  model=claude-4.6-sonnet  agent=coding-assistant
2026-03-20T14:23:15  $ muse status
2026-03-20T14:23:16  EXIT 0  (312ms)
2026-03-20T14:23:20  $ muse commit -m "Refactor velocity normalisation"
2026-03-20T14:23:21  EXIT 0  (891ms)
2026-03-20T14:24:00  SESSION END
```

### Session log format

Each `.jsonl` file contains one JSON object per line:

```jsonl
{"t":"2026-03-20T14:23:11Z","event":"session_start","model_id":"claude-4.6-sonnet","agent_id":"coding-assistant","domain":"midi","branch":"main","repo_root":"/proj","pid":12345}
{"t":"2026-03-20T14:23:15Z","cmd":"muse status","cwd":"/proj","domain":"midi","branch":"main","pid":12345}
{"t":"2026-03-20T14:23:16Z","event":"cmd_end","exit":0,"elapsed_ms":312}
{"t":"2026-03-20T14:24:00Z","event":"session_end","model_id":"claude-4.6-sonnet","agent_id":"coding-assistant","start":"2026-03-20T14:23:11Z"}
```

The `cmd_end` entry always immediately follows the `cmd` entry it closes. All timestamps are UTC ISO-8601.

---

## Visual Tools

### `muse-graph [log flags…]`

Wraps `muse log --graph --oneline` with Python-driven ANSI colorisation:

- Graph chrome (`*`, `|`, `/`, `\`) coloured with the domain's theme colour
- Commit SHAs highlighted in yellow
- `HEAD -> branch` highlighted in bold green
- `[MAJOR]` in red, `[MINOR]` in yellow, `[PATCH]` in green
- `[agent:id]` markers in cyan

Passes any extra flags through to `muse log`:

```zsh
muse-graph -n 20
muse-graph --since "2026-01-01"
```

### `muse-timeline [N]`

Vertical timeline of the last N commits (default 20). Uses domain colours and Unicode box-drawing characters:

```
  ♪ TIMELINE — main (last 5 commits)
  ────────────────────────────────────────────────────────────
  ◉  a1b2c3d4  Add verse melody
  │
  ○  e5f6a7b8  Transpose chorus up 2 semitones
  │
  ○  c9d0e1f2  Add bass line
  │
  ○  a3b4c5d6  Initial commit
  ╵
```

### `muse-diff-preview [ref…]`

Pipes `muse diff` output through `delta` (preferred) or `bat` for syntax
highlighting. Falls back to plain output if neither is installed.

### `muse-commit-browser`

fzf-powered commit browser. Requires `fzf`.

- Left pane: `muse log --oneline`
- Right pane: `muse show <selected>` preview
- `↵` — checkout the selected commit
- `ctrl-d` — show full diff in `less`
- `ctrl-y` — copy commit SHA to clipboard
- `ctrl-s` — open `muse show` in `less`

### `muse-branch-picker`

fzf-powered branch switcher. Also bound to `Ctrl+B`. Requires `fzf`.

- Right pane: last 8 commits on the highlighted branch
- `↵` — checkout the selected branch
- `ctrl-d` — delete the selected branch

### `muse-stash-browser`

fzf-powered stash browser. Requires `fzf`.

- Right pane: `muse stash show` preview
- `↵` — pop the selected stash
- `ctrl-d` — drop the selected stash

---

## Completion Reference

### Top-level commands

All ~50 top-level muse commands are listed with one-line descriptions. Descriptions are intentionally domain-aware (not generic VCS language).

### Per-command argument completion

| Command | What completes |
|---|---|
| `checkout` | branch names + `-b` flag |
| `merge` | branch names + `--strategy` values |
| `branch` | branch names for `-d`, flags |
| `tag` | tag names, short SHAs |
| `push` | remote names, branch names, `--force` |
| `pull` | remote names, branch names, `--rebase` |
| `fetch` | remote names, branch names, `--all` |
| `remote` | `add/remove/rename/list/show/set-url` → remote names |
| `log` | all flags with values where applicable |
| `diff` | short SHAs + `--stat`/`--patch` |
| `show` | short SHAs |
| `reset` | short SHAs + `--hard`/`--soft` |
| `revert` | short SHAs + `--no-commit` |
| `cherry-pick` | short SHAs |
| `blame` | short SHAs |
| `commit` | `--meta key=val`, `--agent-id`, `--model-id`, `--toolchain-id` |
| `stash` | `push/pop/drop/list/show` |
| `config` | `show/get/set/edit` → config key paths |
| `auth` | `login/logout/whoami` |
| `hub` | `connect/status/disconnect/ping` |
| `worktree` | `add/list/remove/prune` |
| `workspace` | `create/list/switch/delete` |
| `bisect` | `start/good/bad/reset/log` |
| `plumbing` | all 12 plumbing subcommands + context-aware ref/remote/file args |
| `midi` | all 25 midi subcommands with MIDI-specific descriptions |
| `code` | all 28 code subcommands with code-analysis descriptions |
| `coord` | all 6 coordination subcommands |
| `clone` | `--branch`, `--depth`, remote URL, local directory |
| `archive` | `--format (tar/zip)`, `--output`, tree-ish |
| `annotate` | tracked file paths |
| `attributes` | `list/check` → tracked file paths |

### Config key completions

| Key | Description |
|---|---|
| `user.name` | Display name (human or agent handle) |
| `user.email` | Email address |
| `user.type` | `"human"` or `"agent"` |
| `hub.url` | MuseHub fabric endpoint URL |
| `domain.ticks_per_beat` | MIDI ticks per beat |
| `domain.default_channel` | MIDI default channel |

---

## Hook System

Set these in `.zshrc` to run custom commands after muse operations:

```zsh
# Desktop notification on commit (macOS)
MUSE_POST_COMMIT_CMD='osascript -e "display notification \"Committed\" with title \"Muse\""'

# Rebuild a local index after checkout
MUSE_POST_CHECKOUT_CMD='make index 2>/dev/null || true'

# Trigger CI after a clean merge
MUSE_POST_MERGE_CMD='curl -X POST https://ci.example.com/trigger'
```

The `MUSE_POST_MERGE_CMD` fires only after a **clean** merge (no conflicts). During a conflicted merge, the hook is suppressed until you resolve and commit.

---

## Keybinding Reference

| Binding | Widget | Action |
|---|---|---|
| `Ctrl+B` | `_muse_widget_branch_picker` | Open fzf branch picker |
| `ESC-M` | `_muse_widget_commit_browser` | Open fzf commit browser |
| `ESC-H` | `_muse_widget_health` | Print repo health and reset prompt |

Set `MUSE_BIND_KEYS=0` to disable all keybindings.

---

## Agent Mode Reference

`MUSE_AGENT_MODE=1` is a master switch that transforms the plugin for agent
orchestration use:

| Feature | Normal mode | Agent mode |
|---|---|---|
| Prompt | `♪ midi:main ✗ 3Δ` | `[midi\|main\|dirty:3\|no-merge]` |
| `$MUSE_CONTEXT_JSON` | Set silently | Also emitted to stderr before each prompt |
| Emoji | Yes | No (ASCII only) |
| `muse_rprompt_info()` | Shows SemVer | Suppressed |

Typical use — start a subshell for an agent, set the flag, and have the
orchestrator read `$MUSE_CONTEXT_JSON` from stderr:

```python
import subprocess, json

proc = subprocess.Popen(
    ["zsh", "--interactive"],
    env={**os.environ, "MUSE_AGENT_MODE": "1"},
    stderr=subprocess.PIPE,
)
# Each prompt draw emits $MUSE_CONTEXT_JSON to stderr
context = json.loads(proc.stderr.readline())
print(context["domain"], context["branch"], context["dirty"])
```

---

## Upgrading

Because the install script uses symlinks, `git pull` inside the muse repo
automatically updates the plugin. Run `source ~/.zshrc` to pick up changes
in the current shell session.

---

## Troubleshooting

**Prompt shows nothing in a muse repo**

1. Verify `muse` is in your `$PATH`: `which muse`
2. Verify you are inside a muse repo: `ls .muse/HEAD`
3. Check `_MUSE_CACHE_VALID` is being set: `echo $_MUSE_CACHE_VALID` (should be 1)
4. Run `_muse_refresh` manually and check for errors

**Dirty check shows `?`**

The `muse status --porcelain` call timed out. Increase `MUSE_DIRTY_TIMEOUT`:

```zsh
MUSE_DIRTY_TIMEOUT=3   # allow 3 seconds before giving up
```

**Completions not showing**

Ensure the `_muse` file is in your `$fpath`. The install script handles this
via symlinks. If you installed manually, add the plugin directory:

```zsh
fpath=("$ZSH_CUSTOM/plugins/muse" $fpath)
autoload -Uz compinit && compinit
```

**`muse-commit-browser` / `muse-branch-picker` not working**

Install [fzf](https://github.com/junegunn/fzf): `brew install fzf` (macOS) or `apt install fzf` (Debian/Ubuntu).

**Session logs not written**

Verify `$MUSE_SESSION_LOG_DIR` exists and is writable:

```zsh
mkdir -p "$MUSE_SESSION_LOG_DIR"
ls -la "$MUSE_SESSION_LOG_DIR"
```
