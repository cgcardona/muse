# muse.plugin.zsh — Oh My ZSH plugin for Muse version control
# ==============================================================================
# Minimal, secure shell integration. Shows domain + branch in your prompt.
# Nothing else runs automatically; everything else is a muse command away.
#
# Setup (after running tools/install-omzsh-plugin.sh):
#   Add $(muse_prompt_info) to your PROMPT in ~/.zshrc, e.g.:
#     PROMPT='%~ $(muse_prompt_info) %# '
#
# Configuration (set in ~/.zshrc BEFORE plugins=(… muse …)):
#   MUSE_PROMPT_ICONS=1      Use emoji icons; set 0 for plain ASCII (default 1)
#   MUSE_DIRTY_TIMEOUT=1     Seconds before dirty-check gives up (default 1)
#
# Security notes:
#   - No eval of any data read from disk or env.
#   - Branch names are regex-validated and %-escaped before prompt display.
#   - Domain name is validated as alphanumeric before use.
#   - All repo paths passed to subprocesses via env vars (not -c strings).
#   - Dirty check runs only after a muse command, never on every keystroke.
#   - Zero subprocesses on prompt render; one python3 on directory change.
# ==============================================================================

autoload -Uz is-at-least
if ! is-at-least 5.0; then
  print "[muse] ZSH 5.0+ required. Plugin not loaded." >&2
  return 1
fi

# ── Configuration ─────────────────────────────────────────────────────────────
: ${MUSE_PROMPT_ICONS:=1}
: ${MUSE_DIRTY_TIMEOUT:=1}

# Domain icon map. Override individual elements in ~/.zshrc before plugins=().
typeset -gA MUSE_DOMAIN_ICONS
MUSE_DOMAIN_ICONS=(
  midi      "♪"
  code      "⌥"
  bitcoin   "₿"
  scaffold  "⬡"
  _default  "◈"
)

# ── Internal state ────────────────────────────────────────────────────────────
typeset -g  MUSE_REPO_ROOT=""   # absolute path to repo root, or ""
typeset -g  MUSE_DOMAIN="midi"  # active domain plugin name
typeset -g  MUSE_BRANCH=""      # branch name, 8-char SHA, or "?"
typeset -gi MUSE_DIRTY=0        # 1 when working tree has uncommitted changes
typeset -gi MUSE_DIRTY_COUNT=0  # number of changed paths
typeset -gi _MUSE_CMD_RAN=0     # 1 after any muse command runs

# ── §1  Core detection (zero subprocesses) ───────────────────────────────────

# Walk up from $PWD to find .muse/. Sets MUSE_REPO_ROOT. Pure ZSH.
function _muse_find_root() {
  local dir="$PWD"
  while [[ "$dir" != "/" ]]; do
    if [[ -d "$dir/.muse" ]]; then
      MUSE_REPO_ROOT="$dir"
      return 0
    fi
    dir="${dir:h}"
  done
  MUSE_REPO_ROOT=""
  return 1
}

# Read branch from .muse/HEAD without forking. Validates before storing.
# Branch names are restricted to [a-zA-Z0-9/_.-] to prevent prompt injection.
function _muse_parse_head() {
  local head_file="$MUSE_REPO_ROOT/.muse/HEAD"
  if [[ ! -f "$head_file" ]]; then
    MUSE_BRANCH=""; return 1
  fi
  local raw
  raw=$(<"$head_file")
  if [[ "$raw" == "refs/heads/"* ]]; then
    local branch="${raw#refs/heads/}"
    # Reject anything that could inject prompt escapes or path components.
    if [[ "$branch" =~ '^[[:alnum:]/_.-]+$' ]]; then
      MUSE_BRANCH="$branch"
    else
      MUSE_BRANCH="?"
    fi
  elif [[ "$raw" =~ '^[0-9a-f]{64}$' ]]; then
    MUSE_BRANCH="${raw:0:8}"  # detached HEAD
  else
    MUSE_BRANCH="?"
  fi
}

# Read domain from .muse/repo.json. One python3 call; path via env var only.
function _muse_parse_domain() {
  local repo_json="$MUSE_REPO_ROOT/.muse/repo.json"
  if [[ ! -f "$repo_json" ]]; then
    MUSE_DOMAIN="midi"; return
  fi
  MUSE_DOMAIN=$(MUSE_REPO_JSON="$repo_json" python3 <<'PYEOF' 2>/dev/null
import json, os
try:
    d = json.load(open(os.environ['MUSE_REPO_JSON']))
    v = str(d.get('domain', 'midi'))
    # Accept only safe domain names: alphanumeric plus hyphens/underscores,
    # max 32 chars. Anything else falls back to 'midi'.
    safe = v.replace('-', '').replace('_', '')
    print(v if (safe.isalnum() and 1 <= len(v) <= 32) else 'midi')
except Exception:
    print('midi')
PYEOF
  )
  : ${MUSE_DOMAIN:=midi}
}

# Check dirty state. Runs with timeout; only called after a muse command.
function _muse_check_dirty() {
  local output rc count=0
  output=$(cd -- "$MUSE_REPO_ROOT" && \
           timeout -- "${MUSE_DIRTY_TIMEOUT}" muse status --porcelain 2>/dev/null)
  rc=$?
  if (( rc == 124 )); then
    # Timeout — leave previous dirty state in place rather than lying.
    return
  fi
  while IFS= read -r line; do
    [[ "$line" == "##"* || -z "$line" ]] && continue
    (( count++ ))
  done <<< "$output"
  MUSE_DIRTY=$(( count > 0 ? 1 : 0 ))
  MUSE_DIRTY_COUNT=$count
}

# ── §2  Cache management ──────────────────────────────────────────────────────

# Lightweight refresh: head + domain only. Called on directory change.
function _muse_refresh() {
  if ! _muse_find_root; then
    MUSE_DOMAIN="midi"; MUSE_BRANCH=""; MUSE_DIRTY=0; MUSE_DIRTY_COUNT=0
    return 1
  fi
  _muse_parse_head
  _muse_parse_domain
}

# Full refresh: head + domain + dirty. Called after a muse command.
function _muse_refresh_full() {
  _muse_refresh || return
  _muse_check_dirty
  _MUSE_CMD_RAN=0
}

# ── §3  ZSH hooks ─────────────────────────────────────────────────────────────

# On directory change: refresh head and domain; clear dirty (stale after cd).
function _muse_hook_chpwd() {
  MUSE_DIRTY=0; MUSE_DIRTY_COUNT=0
  _muse_refresh 2>/dev/null
}
chpwd_functions+=(_muse_hook_chpwd)

# Before a command: flag when the user runs muse so we refresh after.
function _muse_hook_preexec() {
  [[ "${${(z)1}[1]}" == "muse" ]] && _MUSE_CMD_RAN=1
}
preexec_functions+=(_muse_hook_preexec)

# Before the prompt: full refresh only when a muse command just ran.
function _muse_hook_precmd() {
  (( _MUSE_CMD_RAN )) && _muse_refresh_full 2>/dev/null
}
precmd_functions+=(_muse_hook_precmd)

# ── §4  Prompt ────────────────────────────────────────────────────────────────

# Primary prompt segment. Example usage in ~/.zshrc:
#   PROMPT='%~ $(muse_prompt_info) %# '
# Emits nothing when not inside a muse repo.
function muse_prompt_info() {
  [[ -z "$MUSE_REPO_ROOT" ]] && return

  local icon="${MUSE_DOMAIN_ICONS[$MUSE_DOMAIN]:-${MUSE_DOMAIN_ICONS[_default]}}"
  [[ "$MUSE_PROMPT_ICONS" == "0" ]] && icon="[$MUSE_DOMAIN]"

  # Escape % so ZSH does not treat branch-name content as prompt directives.
  local branch="${MUSE_BRANCH//\%/%%}"

  local dirty=""
  (( MUSE_DIRTY )) && dirty=" %F{red}✗ ${MUSE_DIRTY_COUNT}%f"

  echo -n "%F{magenta}${icon} ${branch}%f${dirty}"
}

# ── §5  Aliases ───────────────────────────────────────────────────────────────
alias mst='muse status'
alias msts='muse status --short'
alias mcm='muse commit -m'
alias mco='muse checkout'
alias mlg='muse log'
alias mlgo='muse log --oneline'
alias mlgg='muse log --graph'
alias mdf='muse diff'
alias mdfst='muse diff --stat'
alias mbr='muse branch'
alias mtg='muse tag'
alias mfh='muse fetch'
alias mpull='muse pull'
alias mpush='muse push'
alias mrm='muse remote'

# ── §6  Completion ────────────────────────────────────────────────────────────
if [[ -f "${0:A:h}/_muse" ]]; then
  fpath=("${0:A:h}" $fpath)
  autoload -Uz compinit
  compdef _muse muse 2>/dev/null
fi

# ── Init ──────────────────────────────────────────────────────────────────────
# Warm head + domain on load so the first prompt is not blank.
_muse_refresh 2>/dev/null
