# Muse — Oh My ZSH Plugin

Minimal shell integration for [Muse](https://github.com/cgcardona/muse).
Shows your active domain and branch in the prompt, mirroring what
`git:(branch)` does for Git repos.

---

## Install

```bash
bash /path/to/muse/tools/install-omzsh-plugin.sh
```

Then add `muse` to your `plugins` array in `~/.zshrc`:

```zsh
plugins=(git muse)
```

---

## Prompt setup

Add `$(muse_prompt_info)` wherever you want the indicator in your `PROMPT`:

```zsh
PROMPT='%~ $(muse_prompt_info) %# '
```

Inside a Muse repo this renders as:

```
~/my-song muse:(midi:main) %
```

Outside a Muse repo it emits nothing.

---

## What it shows

| Segment | Meaning |
|---------|---------|
| `muse:(midi:main)` | `midi` domain, branch `main` |
| `muse:(bitcoin:lightning)` | `bitcoin` domain, branch `lightning` |
| `muse:(code:feature/x)` | `code` domain, branch `feature/x` |
| `muse:(scaffold:main)` | `scaffold` domain |
| `muse:(midi:a1b2c3d4)` | detached HEAD (short SHA) |
| `muse:(midi:main) ✗ 3` | dirty working tree, 3 changed paths |

The dirty indicator (`✗ N`) only appears after you run a `muse` command in the
same shell session. This keeps the prompt fast on first open.

---

## Configuration

Set these in `~/.zshrc` **before** `plugins=(… muse …)`:

```zsh
MUSE_PROMPT_ICONS=1       # prepend a domain icon, e.g. ♪ muse:(midi:main)
MUSE_DIRTY_TIMEOUT=1      # seconds before dirty check gives up
```

Domain icons are off by default. To enable and override individual icons:

```zsh
MUSE_PROMPT_ICONS=1
MUSE_DOMAIN_ICONS[midi]="🎵"
MUSE_DOMAIN_ICONS[bitcoin]="🔑"
```

---

## Aliases

| Alias | Command |
|-------|---------|
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

## Tab completion

All top-level `muse` commands and common argument types (branches, tags,
remotes, config keys, subcommands) complete with `<TAB>`.

Completion reads directly from `.muse/refs/` using ZSH globbing — no
subprocesses, no `ls`, instant response.

---

## How it works

1. **On directory change (`chpwd`)** — walks up to find `.muse/`, reads
   `.muse/HEAD` (pure ZSH, no subprocess), reads `.muse/repo.json` for the
   domain (one `python3` call).

2. **After a `muse` command (`precmd`)** — additionally runs
   `muse status --porcelain` with a timeout to update the dirty indicator.

3. **On prompt render** — reads only cached shell variables; zero subprocesses.

---

## Security model

- No `eval` of any data from disk or environment.
- Branch names are regex-validated (`[a-zA-Z0-9/_.-]` only) and
  `%`-escaped before prompt interpolation to prevent ZSH prompt injection.
- Domain names are validated as alphanumeric (max 32 chars) in Python.
- Repo paths are passed to Python via environment variables, never
  interpolated into `-c` strings.
- `cd` and `timeout` calls use `--` to prevent option injection.
- Completion uses ZSH glob patterns, never `ls` or command substitution
  on arbitrary file content.
