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
: ${MUSE_PROMPT_ICONS:=0}
: ${MUSE_DIRTY_TIMEOUT:=5}

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

# Walk up from $PWD to find a valid Muse repo root. Sets MUSE_REPO_ROOT.
# A valid repo requires .muse/repo.json — a bare .muse/ directory is not
# enough. This prevents false positives from stray or partial .muse/ dirs
# (e.g. a forgotten muse init in a parent directory).
function _muse_find_root() {
  local dir="$PWD"
  while [[ "$dir" != "/" ]]; do
    if [[ -f "$dir/.muse/repo.json" ]]; then
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
#
# Muse HEAD format (canonical, written by muse/core/store.py):
#   ref: refs/heads/<branch>   — on a branch (symbolic ref)
#   commit: <sha256>           — detached HEAD (direct commit reference)
function _muse_parse_head() {
  local head_file="$MUSE_REPO_ROOT/.muse/HEAD"
  if [[ ! -f "$head_file" ]]; then
    MUSE_BRANCH=""; return 1
  fi
  local raw
  raw=$(<"$head_file")
  if [[ "$raw" == "ref: refs/heads/"* ]]; then
    local branch="${raw#ref: refs/heads/}"
    # Reject anything that could inject prompt escapes or path components.
    if [[ "$branch" =~ '^[[:alnum:]/_.-]+$' ]]; then
      MUSE_BRANCH="$branch"
    else
      MUSE_BRANCH="?"
    fi
  elif [[ "$raw" == "commit: "* ]]; then
    local sha="${raw#commit: }"
    MUSE_BRANCH="${sha:0:8}"  # detached HEAD — show short SHA
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

# Check dirty state. Runs with timeout; called on cd, shell load, and after
# any muse command. MUSE_DIRTY_TIMEOUT (default 5s) caps the wait.
typeset -gi _MUSE_LAST_DIRTY_RC=0  # last exit code from the dirty check subprocess
typeset -gi _MUSE_REFRESHING=0    # re-entry guard: 1 while a refresh is in progress

function _muse_check_dirty() {
  local output rc count=0
  # Run in a ZSH subshell (not env) so muse is found via PATH/aliases/venv.
  # The cd here would normally re-fire chpwd_functions inside the subshell and
  # recurse infinitely, but _muse_hook_chpwd's re-entry guard (_MUSE_REFRESHING)
  # is inherited by subshells and blocks any nested call immediately.
  output=$(cd -- "$MUSE_REPO_ROOT" && \
           timeout -- "${MUSE_DIRTY_TIMEOUT}" muse status --porcelain 2>/dev/null)
  rc=$?
  _MUSE_LAST_DIRTY_RC=$rc
  if (( rc == 124 )); then
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

# Full refresh: head + domain + dirty. Called on directory change and on load.
# One muse subprocess (status --porcelain) runs every time — same model as the
# git plugin. The timeout in _muse_check_dirty keeps it bounded.
function _muse_refresh() {
  if ! _muse_find_root; then
    MUSE_DOMAIN="midi"; MUSE_BRANCH=""; MUSE_DIRTY=0; MUSE_DIRTY_COUNT=0
    return 1
  fi
  _muse_parse_head
  _muse_parse_domain
  _muse_check_dirty
}

# Post-command refresh: same as _muse_refresh but resets the command flag.
function _muse_refresh_full() {
  _muse_refresh || return
  _MUSE_CMD_RAN=0
}

# ── §3  ZSH hooks ─────────────────────────────────────────────────────────────

# On directory change: refresh head and domain; clear dirty (stale after cd).
# Pre-clear MUSE_REPO_ROOT so any silent failure in _muse_refresh leaves the
# prompt blank rather than showing stale data from the previous directory.
function _muse_hook_chpwd() {
  # Guard: ZSH fires chpwd_functions inside $(…) subshells too. Without this,
  # the cd inside _muse_check_dirty triggers this hook inside the subshell,
  # which calls _muse_refresh → _muse_check_dirty → cd → chpwd → ∞.
  # Subshells inherit _MUSE_REFRESHING, so the guard stops nested calls cold.
  (( _MUSE_REFRESHING )) && return
  _MUSE_REFRESHING=1
  MUSE_REPO_ROOT=""; MUSE_BRANCH=""; MUSE_DIRTY=0; MUSE_DIRTY_COUNT=0
  _muse_refresh
  _MUSE_REFRESHING=0
}
chpwd_functions+=(_muse_hook_chpwd)

# Before a command: flag when the user runs muse so we refresh after.
function _muse_hook_preexec() {
  [[ "${${(z)1}[1]}" == "muse" ]] && _MUSE_CMD_RAN=1
}
preexec_functions+=(_muse_hook_preexec)

# Before every prompt: refresh dirty state so any file change (touch, vim,
# cp, etc.) is reflected immediately — same model as the git plugin.
# After a muse command: full refresh (head + domain + dirty).
# Otherwise: dirty-only refresh when inside a repo.
function _muse_hook_precmd() {
  if (( _MUSE_CMD_RAN )); then
    _muse_refresh_full
  elif [[ -n "$MUSE_REPO_ROOT" ]]; then
    _muse_check_dirty
  fi
}
precmd_functions+=(_muse_hook_precmd)

# ── §4  Prompt ────────────────────────────────────────────────────────────────

# Primary prompt segment. Example usage in ~/.zshrc:
#   PROMPT='%~ $(muse_prompt_info) %# '
# Emits nothing when not inside a muse repo.
#
# Clean:  muse:(code:main)       — domain:branch in magenta
# Dirty:  muse:(code:main)       — domain:branch in yellow
#
# The color of the branch text is the only dirty signal — no extra symbol.
# Yellow means "uncommitted changes exist"; magenta means clean.
function muse_prompt_info() {
  [[ -z "$MUSE_REPO_ROOT" ]] && return

  # Escape % so ZSH does not treat branch-name content as prompt directives.
  local branch="${MUSE_BRANCH//\%/%%}"
  local domain="${MUSE_DOMAIN//\%/%%}"

  # Branch: yellow when dirty, magenta when clean. Domain is always magenta.
  local branch_color="%F{magenta}"
  (( MUSE_DIRTY )) && branch_color="%F{yellow}"

  if [[ "$MUSE_PROMPT_ICONS" == "1" ]]; then
    local icon="${MUSE_DOMAIN_ICONS[$MUSE_DOMAIN]:-${MUSE_DOMAIN_ICONS[_default]}}"
    echo -n "%F{cyan}${icon} muse:(%F{magenta}${domain}:${branch_color}${branch}%F{cyan})%f"
  else
    echo -n "%F{cyan}muse:(%F{magenta}${domain}:${branch_color}${branch}%F{cyan})%f"
  fi
}

# ── §5  Debug ─────────────────────────────────────────────────────────────────

# Print current plugin state. Run when the prompt looks wrong.
#   muse_debug
function muse_debug() {
  print "MUSE_REPO_ROOT       = ${MUSE_REPO_ROOT:-(not set)}"
  print "MUSE_BRANCH          = ${MUSE_BRANCH:-(not set)}"
  print "MUSE_DOMAIN          = ${MUSE_DOMAIN:-(not set)}"
  print "MUSE_DIRTY           = $MUSE_DIRTY  (count: $MUSE_DIRTY_COUNT)"
  print "MUSE_DIRTY_TIMEOUT   = ${MUSE_DIRTY_TIMEOUT}s"
  print "_MUSE_LAST_DIRTY_RC  = $_MUSE_LAST_DIRTY_RC  (124 = timed out)"
  print "_MUSE_CMD_RAN        = $_MUSE_CMD_RAN"
  print "PWD                  = $PWD"
  if [[ -n "$MUSE_REPO_ROOT" ]]; then
    print "repo.json            = $MUSE_REPO_ROOT/.muse/repo.json"
    print "HEAD                 = $(< "$MUSE_REPO_ROOT/.muse/HEAD" 2>/dev/null || print '(missing)')"
    print "--- muse status --porcelain (live, timed) ---"
    time (cd -- "$MUSE_REPO_ROOT" && muse status --porcelain 2>&1)
    print "--------------------------------------------"
  fi
}

# ── §6  Aliases ───────────────────────────────────────────────────────────────
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

# ── §7  Completion ────────────────────────────────────────────────────────────
if [[ -f "${0:A:h}/_muse" ]]; then
  fpath=("${0:A:h}" $fpath)
  autoload -Uz compinit
  compdef _muse muse 2>/dev/null
fi

# ── §8  Init ──────────────────────────────────────────────────────────────────
# Warm head + domain on load so the first prompt is not blank.
_muse_refresh
