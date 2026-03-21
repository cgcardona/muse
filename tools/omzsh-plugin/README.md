# Muse — Oh My ZSH Plugin

Domain-aware, agent-native ZSH integration for [Muse](https://github.com/gabrielchua/muse) — version control for multidimensional state.

Unlike the Git plugin which treats every repo identically, this plugin **knows what kind of state you are versioning** — MIDI, code, genomics, spatial data — and surfaces domain-specific information at every layer: the prompt, the completions, the workflow functions, and the agent session system.

---

## Installation

**One-liner** (run from the muse repo root):

```zsh
bash tools/install-omzsh-plugin.sh
```

Then add `muse` to your `plugins` array in `~/.zshrc`:

```zsh
plugins=(git muse)   # add 'muse' alongside your existing plugins
```

Reload:

```zsh
source ~/.zshrc
```

The install script creates symlinks from `~/.oh-my-zsh/custom/plugins/muse/` back into the repo, so the plugin stays up to date as you pull new Muse releases.

---

## Prompt Setup

### Oh My ZSH (default theme)

Add `$(muse_prompt_info)` to your `$PROMPT` in `~/.zshrc`:

```zsh
PROMPT='%~ $(muse_prompt_info) %# '
RPROMPT='$(muse_rprompt_info)'
```

### Powerlevel10k

Add `muse_vcs` to your prompt element arrays in `~/.p10k.zsh`:

```zsh
POWERLEVEL9K_LEFT_PROMPT_ELEMENTS=(… muse_vcs …)
# or
POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS=(… muse_vcs muse_rprompt …)
```

The plugin provides both `prompt_muse_vcs()` (async) and `instant_prompt_muse_vcs()` (instant prompt) so your prompt is never blank during p10k's async refresh.

### What the prompt looks like

| State | Prompt |
|---|---|
| Clean, midi domain | `♪ midi:main ✓` |
| Dirty (3 changed paths) | `♪ midi:main ✗ 3Δ` |
| Mid-merge (2 conflicts) | `♪ midi:main ⚡ ← feature/exp (2 conflicts)` |
| Detached HEAD | `♪ midi:(detached:a1b2c3d4)` |
| Agent session active | `♪ midi:main ✗ 3Δ [🤖 claude-4.6-sonnet]` |
| `MUSE_AGENT_MODE=1` | `[midi\|main\|dirty:3\|no-merge]` |
| `MUSE_AGENT_MODE=1` + merge | `[midi\|main\|dirty:3\|merging:feature/exp:2]` |

The right-prompt segment shows the SemVer bump of the HEAD commit:

| SemVer | Right prompt |
|---|---|
| major | `[MAJOR]` (red) |
| minor | `[MINOR]` (yellow) |
| patch | `[PATCH]` (green) |

Domain icons are user-overridable:

```zsh
MUSE_DOMAIN_ICONS[midi]="♩"      # change just the midi icon
MUSE_DOMAIN_ICONS[genomics]="🔬"  # add a new domain before it exists in core
```

---

## Configuration

Set these in `~/.zshrc` **before** `plugins=(… muse …)`:

| Variable | Default | Effect |
|---|---|---|
| `MUSE_PROMPT_SHOW_DOMAIN` | `1` | Include domain name in prompt |
| `MUSE_PROMPT_SHOW_OPS` | `1` | Include dirty-path count (the `3Δ` indicator) |
| `MUSE_PROMPT_ICONS` | `1` | Use emoji icons; set to `0` for ASCII fallback |
| `MUSE_AGENT_MODE` | `0` | Machine-parseable mode (see Agent Mode below) |
| `MUSE_DIRTY_TIMEOUT` | `1` | Seconds before dirty check gives up (shows `?`) |
| `MUSE_SESSION_LOG_DIR` | `~/.muse/sessions` | Where session `.jsonl` logs are written |
| `MUSE_BIND_KEYS` | `1` | Bind `Ctrl+B` / `ESC-M` / `ESC-H` shortcuts |
| `MUSE_POST_COMMIT_CMD` | _(empty)_ | Shell command run after each `muse commit` |
| `MUSE_POST_CHECKOUT_CMD` | _(empty)_ | Shell command run after each `muse checkout` |
| `MUSE_POST_MERGE_CMD` | _(empty)_ | Shell command run after a clean `muse merge` |

**Example** — desktop notification on commit:

```zsh
MUSE_POST_COMMIT_CMD='osascript -e "display notification \"Committed!\" with title \"Muse\""'
```

---

## Aliases

All aliases use the `m` prefix to avoid collisions with system commands.

### Core VCS

| Alias | Expands to |
|---|---|
| `mst` | `muse status` |
| `msts` | `muse status --short` |
| `mstp` | `muse status --porcelain` |
| `mcm` | `muse commit -m` |
| `mco` | `muse checkout` |
| `mlg` | `muse log` |
| `mlgo` | `muse log --oneline` |
| `mlgg` | `muse log --graph` |
| `mlggs` | `muse log --graph --oneline` |
| `mdf` | `muse diff` |
| `mdfst` | `muse diff --stat` |
| `mdfp` | `muse diff --patch` |
| `mbr` | `muse branch` |
| `mbrv` | `muse branch -v` |
| `msh` | `muse show` |
| `mbl` | `muse blame` |
| `mrl` | `muse reflog` |
| `mtg` | `muse tag` |

### Stash

| Alias | Expands to |
|---|---|
| `msta` | `muse stash` |
| `mstap` | `muse stash pop` |
| `mstal` | `muse stash list` |
| `mstad` | `muse stash drop` |

### Remotes

| Alias | Expands to |
|---|---|
| `mfh` | `muse fetch` |
| `mpull` | `muse pull` |
| `mpush` | `muse push` |
| `mrm` | `muse remote` |
| `mclone` | `muse clone` |

### Domain & plumbing shortcuts

| Alias | Expands to |
|---|---|
| `mmidi` | `muse midi` |
| `mcode` | `muse code` |
| `mcoord` | `muse coord` |
| `mplumb` | `muse plumbing` |
| `mcfg` | `muse config` |
| `mhub` | `muse hub` |

---

## Workflow Functions

### Branch management

```zsh
muse-new-feat drums-pattern    # creates and switches to feat/drums-pattern
muse-new-fix bad-velocity      # creates and switches to fix/bad-velocity
muse-new-refactor engine-core  # creates and switches to refactor/engine-core
```

### Commits

```zsh
muse-wip                  # commit with auto-timestamp "[WIP] 2026-03-20T14:30:00Z"
muse-quick-commit         # interactive guided commit (domain-aware metadata prompts)
muse-agent-commit "msg"   # commit with agent session identity auto-injected
```

### Sync & merge

```zsh
muse-sync                 # fetch + pull + status
muse-safe-merge feature/x # merge with conflict list + editor launch on failure
```

### Health & provenance

```zsh
muse-health               # repo health summary (dirty, merge, stashes, remotes)
muse-who-last             # show HEAD commit author (human or agent + model)
muse-agent-blame 20       # show authorship breakdown for last 20 commits
muse-overview             # domain-specific live state overview
```

---

## Keybindings

| Key | Action |
|---|---|
| `Ctrl+B` | Open branch picker (fzf) |
| `ESC-M` | Open commit browser (fzf) |
| `ESC-H` | Show repo health summary |

Set `MUSE_BIND_KEYS=0` in `.zshrc` to disable all keybindings.

---

## Tab Completion

The plugin registers `_muse` as the completion function for the `muse` command.
Completions are provided for:

- All ~50 top-level commands with descriptions
- Branch names (`checkout`, `merge`, `branch -d`, …)
- Tag names (`tag create`, `tag -d`, …)
- Remote names (`push`, `pull`, `fetch`, `remote`, …)
- Short SHAs (`show`, `diff`, `reset`, `revert`, `cherry-pick`, …)
- Config key paths (`config get`, `config set`)
- All `plumbing` subcommands (12)
- All `midi` subcommands (25) with MIDI-specific descriptions
- All `code` subcommands (28) with code-analysis descriptions
- All `coord` subcommands (6)
- Common flags per command (`log`, `diff`, `commit`, `status`, …)

Branch, tag, and remote lookups read directly from `.muse/` — no subprocess.
File-argument completions (`midi notes <tab>`) use `muse plumbing ls-files`.

---

## Visual Tools (fzf optional)

Install [fzf](https://github.com/junegunn/fzf) to unlock the interactive tools:

```zsh
muse-commit-browser    # fzf over commit log; ↵=checkout, ctrl-d=diff, ctrl-y=copy SHA
muse-branch-picker     # fzf over branch list; ↵=checkout, ctrl-d=delete
muse-stash-browser     # fzf over stash list; ↵=pop, ctrl-d=drop
```

Install [bat](https://github.com/sharkdp/bat) or [delta](https://github.com/dandavison/delta) for syntax-highlighted diff output:

```zsh
muse-diff-preview             # muse diff with bat/delta highlighting
muse-diff-preview feature/x   # diff against a branch
```

Graph and timeline visualisations work without fzf:

```zsh
muse-graph           # colorised commit graph (domain-themed, SemVer badges)
muse-timeline 30     # visual vertical timeline of last 30 commits
```

---

## Agent Mode

Muse is built for AI agents as first-class authors. The plugin reflects this throughout.

### Machine-readable prompt

```zsh
export MUSE_AGENT_MODE=1
```

Prompt becomes: `[midi|main|dirty:3|no-merge|agent:coding-assistant]`

All workflow functions emit JSON instead of human text. `$MUSE_CONTEXT_JSON` is emitted to stderr before every prompt so an orchestrating process can read it.

### `$MUSE_CONTEXT_JSON`

Always set when inside a muse repo. Updated before every prompt. Format:

```json
{
  "schema_version": 1,
  "domain": "midi",
  "branch": "main",
  "repo_root": "/path/to/project",
  "dirty": true,
  "dirty_count": 3,
  "merging": false,
  "merge_branch": null,
  "conflict_count": 0,
  "user_type": "human",
  "agent_id": null,
  "model_id": null,
  "semver": "minor"
}
```

Agents can read this without running any subcommands:

```zsh
echo $MUSE_CONTEXT_JSON | python3 -m json.tool
```

### `muse-context`

Human-readable, TOML, or JSON context block designed for AI context injection:

```zsh
muse-context           # human-readable block
muse-context --json    # pretty-printed JSON
muse-context --toml    # TOML
muse-context --oneline # single-line summary: "midi:main dirty:3 commit:a1b2c3d4"
```

### Agent session management

```zsh
# Begin a session — sets identity vars, starts JSONL audit log
muse-agent-session claude-4.6-sonnet coding-assistant

# Every muse commit in this shell now auto-injects agent_id + model_id
muse-agent-commit "Refactor velocity normalisation"
# equivalent to: muse commit -m "…" --agent-id coding-assistant --model-id claude-4.6-sonnet

# End the session
muse-agent-end
```

### Session logs

Every `muse` command run during an agent session is logged to a `.jsonl` file:

```json
{"t":"2026-03-20T14:23:11Z","cmd":"muse commit -m refactor","cwd":"/proj","domain":"midi","branch":"feat/x","pid":1234}
{"t":"2026-03-20T14:23:11Z","event":"cmd_end","exit":0,"elapsed_ms":312}
```

List and replay sessions:

```zsh
muse-sessions                         # list recent sessions
muse-sessions ~/.muse/sessions/...    # replay a session log
```

---

## Dependencies

| Tool | Required | Purpose |
|---|---|---|
| `python3` | Yes | JSON/TOML parsing (always available — Muse requires it) |
| `fzf` | Optional | Interactive branch picker, commit browser, stash browser |
| `bat` | Optional | Syntax-highlighted diff preview |
| `delta` | Optional | Enhanced diff pager |
| ZSH 5.0+ | Yes | Associative arrays, `autoload -Uz`, `EPOCHSECONDS` |

---

## Starship users

Add a custom command to `~/.config/starship.toml`:

```toml
[custom.muse]
command = "muse-context --oneline 2>/dev/null"
when = "test -d .muse || git rev-parse --git-dir 2>/dev/null | grep -q .muse"
shell = ["zsh", "-c"]
format = "[$output]($style) "
style = "bold magenta"
```

This gives you the one-line context (`midi:main dirty:3 commit:a1b2c3d4`) in any Starship prompt.
