# Oh My ZSH Plugin — Reference

Minimal, secure ZSH integration for Muse. Provides a prompt segment, core
aliases, and tab completion. Nothing runs automatically beyond what is needed to
keep the prompt accurate.

---

## Files

| File | Purpose |
|------|---------|
| `tools/omzsh-plugin/muse.plugin.zsh` | Main plugin (~230 lines) |
| `tools/omzsh-plugin/_muse` | ZSH completion function |
| `tools/install-omzsh-plugin.sh` | Symlink installer |

---

## Prompt segment

```zsh
# In ~/.zshrc
PROMPT='%~ $(muse_prompt_info) %# '
```

`muse_prompt_info` emits nothing outside a Muse repo. Inside one it emits:

```
%F{cyan}muse:(%F{<color>}<domain>:<branch>%F{cyan})%f
```

The inner color is the only dirty signal — no extra symbol or count:

- **magenta** — working tree is clean
- **yellow** — uncommitted changes exist

This mirrors the Git plugin's `git:(branch)` format, extended with the domain
name — the key differentiator from a single-domain VCS.

### Examples

| Output | Inner color | Meaning |
|--------|-------------|---------|
| `muse:(midi:main)` | magenta | clean |
| `muse:(midi:main)` | yellow | uncommitted changes |
| `muse:(midi:a1b2c3d4)` | magenta | detached HEAD, clean |
| `muse:(midi:a1b2c3d4)` | yellow | detached HEAD, uncommitted changes |

The yellow state only appears after a `muse` command runs in the current shell.

### Domain icons (optional)

Icons are off by default. Set `MUSE_PROMPT_ICONS=1` to prepend one:

| Domain | Icon | Config key |
|--------|------|-----------|
| `midi` | `♪` | `MUSE_DOMAIN_ICONS[midi]` |
| `code` | `⌥` | `MUSE_DOMAIN_ICONS[code]` |
| `bitcoin` | `₿` | `MUSE_DOMAIN_ICONS[bitcoin]` |
| `scaffold` | `⬡` | `MUSE_DOMAIN_ICONS[scaffold]` |
| (unknown) | `◈` | `MUSE_DOMAIN_ICONS[_default]` |

With icons on, the prompt becomes `♪ muse:(midi:main)`.

---

## Environment variables

### Configuration (set before `plugins=(… muse …)`)

| Variable | Default | Meaning |
|----------|---------|---------|
| `MUSE_PROMPT_ICONS` | `0` | `1` prepends a domain icon before `muse:(…)` |
| `MUSE_DIRTY_TIMEOUT` | `1` | Seconds before dirty check aborts |

### State (read-only, exported by plugin)

| Variable | Type | Meaning |
|----------|------|---------|
| `MUSE_REPO_ROOT` | string | Absolute path to repo root, or `""` |
| `MUSE_DOMAIN` | string | Active domain name |
| `MUSE_BRANCH` | string | Branch name, short SHA, or `?` |
| `MUSE_DIRTY` | integer | `1` if working tree has changes |
| `MUSE_DIRTY_COUNT` | integer | Number of changed paths |

---

## Hooks

| Hook | When it fires | What it does |
|------|--------------|--------------|
| `chpwd` | On `cd` | Re-finds repo root, re-reads HEAD and domain; clears dirty state |
| `preexec` | Before any command | Sets `_MUSE_CMD_RAN=1` when command is `muse` |
| `precmd` | Before prompt | Runs full refresh (including dirty check) only if `_MUSE_CMD_RAN=1` |

---

## Aliases

| Alias | Expands to |
|-------|-----------|
| `mst` | `muse status` |
| `msts` | `muse status --short` |
| `mcm` | `muse commit -m` |
| `mco` | `muse checkout` |
| `mlg` | `muse log` |
| `mlgo` | `muse log --oneline` |
| `mlgg` | `muse log --graph` |
| `mdf` | `muse diff` |
| `mdfst` | `muse diff --stat` |
| `mbr` | `muse branch` |
| `mtg` | `muse tag` |
| `mfh` | `muse fetch` |
| `mpull` | `muse pull` |
| `mpush` | `muse push` |
| `mrm` | `muse remote` |

---

## Completion

The `_muse` completion function handles:

- Top-level command names with descriptions.
- Branch names for `checkout`, `merge`, `cherry-pick`, `branch`, `reset`, `revert`, `diff`, `show`, `blame`.
- Remote names for `push`, `pull`, `fetch`.
- Tag names for `tag`.
- Config key suggestions for `config`.
- Subcommand names for `stash`, `remote`, `plumbing`, `commit` flags.

All branch/tag/remote lookups use ZSH glob patterns against `.muse/refs/` and
`.muse/remotes/` — no subprocess, no `ls`, instant.

---

## Performance model

| Trigger | Subprocesses | What runs |
|---------|-------------|-----------|
| Prompt render | 0 | Reads cached shell vars only |
| `cd` into repo | 1 (`python3`) | HEAD (ZSH read) + domain (python3) |
| `cd` outside repo | 0 | Clears vars only |
| After `muse` command | 1 (`muse status`) | Full refresh + dirty check |
| Tab completion | 0 | ZSH glob reads `.muse/refs/` |

---

## Security model

### Branch name injection

`.muse/HEAD` is read with a pure ZSH `$(<file)` — no subprocess. Muse writes
HEAD in one of two self-describing forms (set by `muse/core/store.py`):

```
ref: refs/heads/<branch>   — on a branch
commit: <sha256>           — detached HEAD
```

The branch name is validated with `[[ "$branch" =~ '^[[:alnum:]/_.-]+$' ]]`.
Any name containing characters outside this set (including `%`, `$`, backticks,
quotes) is replaced with `?`. Valid names are additionally `%`-escaped
(`${branch//\%/%%}`) before insertion into the prompt string, so ZSH never
interprets them as prompt directives.

### Domain injection

The domain value from `.muse/repo.json` is extracted by `python3` and validated
with `safe.isalnum() and 1 <= len(v) <= 32` before printing. The path to
`repo.json` is passed via the `MUSE_REPO_JSON` environment variable — never
interpolated into a `-c` string — so a path containing single quotes, spaces,
or special characters is handled safely.

### Path injection

`cd -- "$MUSE_REPO_ROOT"` uses `--` so the path cannot be interpreted as a
flag. `timeout -- ...` follows the same pattern.

### No `eval`

No user-supplied data is ever passed to `eval`.

### Completion safety

The completion function uses ZSH glob expansion (`${refs_dir}/*(N:t)`) instead
of `$(ls ...)` to enumerate branches. This avoids word-splitting on filenames
that contain spaces, and prevents `ls` output from being treated as shell tokens.

---

## Installation

```bash
bash tools/install-omzsh-plugin.sh
```

The script creates a symlink from `~/.oh-my-zsh/custom/plugins/muse/` to
`tools/omzsh-plugin/`. Because it is a symlink, pulling new commits to the Muse
repo automatically updates the plugin.

Add to `~/.zshrc`:

```zsh
plugins=(git muse)
```

Then add the prompt segment:

```zsh
# Append to your existing PROMPT, or set a new one:
PROMPT+='$(muse_prompt_info) '
```

Then reload:

```zsh
exec zsh
```
