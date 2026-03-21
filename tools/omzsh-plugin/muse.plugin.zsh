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
: ${MUSE_DEBUG:=0}          # set to 1 to print timestamped trace to stderr

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

function _muse_check_dirty() {
  local output rc count=0
  print "[muse:dirty] running: muse status --porcelain in $MUSE_REPO_ROOT" >&2
  output=$(cd -- "$MUSE_REPO_ROOT" && \
           timeout -- "${MUSE_DIRTY_TIMEOUT}" muse status --porcelain 2>&1)
  rc=$?
  _MUSE_LAST_DIRTY_RC=$rc
  print "[muse:dirty] rc=$rc  output=$(echo $output | head -c 200)" >&2
  if (( rc == 124 )); then
    print "[muse:dirty] TIMED OUT — MUSE_DIRTY unchanged ($MUSE_DIRTY)" >&2
    return
  fi
  while IFS= read -r line; do
    [[ "$line" == "##"* || -z "$line" ]] && continue
    (( count++ ))
  done <<< "$output"
  MUSE_DIRTY=$(( count > 0 ? 1 : 0 ))
  MUSE_DIRTY_COUNT=$count
  print "[muse:dirty] MUSE_DIRTY=$MUSE_DIRTY  count=$count" >&2
}

# ── §2  Cache management ──────────────────────────────────────────────────────

# Full refresh: head + domain + dirty. Called on directory change and on load.
# One muse subprocess (status --porcelain) runs every time — same model as the
# git plugin. The timeout in _muse_check_dirty keeps it bounded.
function _muse_refresh() {
  (( MUSE_DEBUG )) && print "[muse] _muse_refresh start  $(date +%T.%3N)" >&2
  if ! _muse_find_root; then
    MUSE_DOMAIN="midi"; MUSE_BRANCH=""; MUSE_DIRTY=0; MUSE_DIRTY_COUNT=0
    (( MUSE_DEBUG )) && print "[muse] _muse_find_root: no repo" >&2
    return 1
  fi
  (( MUSE_DEBUG )) && print "[muse] root=$MUSE_REPO_ROOT" >&2
  _muse_parse_head
  (( MUSE_DEBUG )) && print "[muse] head done  branch=$MUSE_BRANCH  $(date +%T.%3N)" >&2
  _muse_parse_domain
  (( MUSE_DEBUG )) && print "[muse] domain done  domain=$MUSE_DOMAIN  $(date +%T.%3N)" >&2
  _muse_check_dirty
  (( MUSE_DEBUG )) && print "[muse] dirty done  dirty=$MUSE_DIRTY rc=$_MUSE_LAST_DIRTY_RC  $(date +%T.%3N)" >&2
}

# Post-command refresh: same as _muse_refresh but resets the command flag.
function _muse_refresh_full() {
  (( MUSE_DEBUG )) && print "[muse] _muse_refresh_full (cmd_ran=$_MUSE_CMD_RAN)" >&2
  _muse_refresh || return
  _MUSE_CMD_RAN=0
}

# ── §3  ZSH hooks ─────────────────────────────────────────────────────────────

# On directory change: refresh head and domain; clear dirty (stale after cd).
# Pre-clear MUSE_REPO_ROOT so any silent failure in _muse_refresh leaves the
# prompt blank rather than showing stale data from the previous directory.
function _muse_hook_chpwd() {
  MUSE_REPO_ROOT=""; MUSE_BRANCH=""; MUSE_DIRTY=0; MUSE_DIRTY_COUNT=0
  _muse_refresh
}
chpwd_functions+=(_muse_hook_chpwd)

# Before a command: flag when the user runs muse so we refresh after.
function _muse_hook_preexec() {
  [[ "${${(z)1}[1]}" == "muse" ]] && _MUSE_CMD_RAN=1
}
preexec_functions+=(_muse_hook_preexec)

# Before the prompt: full refresh only when a muse command just ran.
function _muse_hook_precmd() {
  print "[muse:precmd] _MUSE_CMD_RAN=$_MUSE_CMD_RAN" >&2
  (( _MUSE_CMD_RAN )) && _muse_refresh_full
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
# The color of the domain:branch text is the only dirty signal — no extra
# symbol. Yellow means "uncommitted changes exist"; magenta means clean.
function muse_prompt_info() {
  [[ -z "$MUSE_REPO_ROOT" ]] && return

  local _dbg_color; (( MUSE_DIRTY )) && _dbg_color=YELLOW || _dbg_color=MAGENTA
  print "[muse:prompt] MUSE_DIRTY=$MUSE_DIRTY → color=$_dbg_color" >&2

  # Escape % so ZSH does not treat branch-name content as prompt directives.
  local branch="${MUSE_BRANCH//\%/%%}"
  local domain="${MUSE_DOMAIN//\%/%%}"

  # Branch: magenta when clean, yellow when dirty. Domain is always magenta.
  local branch_color="%F{magenta}"
  (( MUSE_DIRTY )) && branch_color="%F{yellow}"

  # Format: %F{cyan}muse:(%F{magenta}<domain>:%F{yellow|magenta}<branch>%F{cyan})%f
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
_muse_refresh 2>/dev/null
