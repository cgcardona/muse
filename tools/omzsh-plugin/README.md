# Muse — Oh My ZSH Plugin

Minimal shell integration for [Muse](https://github.com/cgcardona/muse).
Shows your active domain and branch in the prompt. That's it.

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
~/my-song ♪ main %
```

Outside a Muse repo it emits nothing.

---

## What it shows

| Segment | Meaning |
|---------|---------|
| `♪ main` | `midi` domain, branch `main` |
| `₿ lightning` | `bitcoin` domain, branch `lightning` |
| `⌥ feature/x` | `code` domain, branch `feature/x` |
| `◈ scaffold` | unknown/default domain |
| `♪ a1b2c3d4` | detached HEAD (short SHA) |
| `♪ main ✗ 3` | dirty working tree, 3 changed paths |

The dirty indicator (`✗`) only appears after you run a `muse` command in the
same shell session. This keeps the prompt fast on first open.

---

## Configuration

Set these in `~/.zshrc` **before** `plugins=(… muse …)`:

```zsh
MUSE_PROMPT_ICONS=1       # 0 = ASCII fallback, e.g. [midi] main
MUSE_DIRTY_TIMEOUT=1      # seconds before dirty check gives up
```

Override individual domain icons:

```zsh
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
