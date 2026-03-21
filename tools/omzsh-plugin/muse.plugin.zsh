# ==============================================================================
# muse.plugin.zsh — Oh My ZSH plugin for Muse version control
# ==============================================================================
#
# Domain-aware, agent-native ZSH integration for Muse: version control for
# multidimensional state. Music today, genomics/code/spacetime tomorrow.
#
# Sections
#   §0   Configuration knobs (set in .zshrc before plugins=(… muse …))
#   §1   Internal state ($MUSE_* env vars, $_MUSE_* private cache vars)
#   §2   Core detection  (zero-subprocess file reads for HEAD, domain, meta)
#   §3   Cache management (warm/refresh/invalidate logic)
#   §4   ZSH hooks        (chpwd, preexec, precmd)
#   §5   Prompt functions (muse_prompt_info, muse_rprompt_info, p10k segment)
#   §6   Aliases          (30+ m-prefixed shortcuts, domain shortcuts)
#   §7   Workflow functions (muse-new-feat, muse-safe-merge, muse-quick-commit…)
#   §8   Agent-native     (muse-context, muse-agent-session, session replay…)
#   §9   Visual tools     (muse-graph, muse-timeline, fzf browsers…)
#   §10  Powerlevel10k    (prompt_muse_vcs, instant_prompt_muse_vcs)
#   §11  Keybindings      (Ctrl+B branch picker, ESC-M commit browser)
#   §12  Hook system      (user post-commit/checkout/merge callbacks)
#   §13  Completion       (registration of the _muse completion file)
#   §14  Initialisation   (warm the cache on plugin load)
#
# Quick start
#   1. Run tools/install-omzsh-plugin.sh from the muse repo root.
#   2. Add 'muse' to plugins=(...) in ~/.zshrc.
#   3. Reload: source ~/.zshrc
#
# Configuration (set these BEFORE sourcing / before plugins=() in .zshrc):
#   MUSE_PROMPT_SHOW_DOMAIN=1   Include domain name in prompt (default 1)
#   MUSE_PROMPT_SHOW_OPS=1      Include dirty-path count in prompt (default 1)
#   MUSE_PROMPT_ICONS=1         Use emoji icons; 0 for plain ASCII (default 1)
#   MUSE_AGENT_MODE=0           Machine-parseable mode for AI agents (default 0)
#   MUSE_DIRTY_TIMEOUT=1        Seconds before dirty-check gives up (default 1)
#   MUSE_SESSION_LOG_DIR        Session log directory (default ~/.muse/sessions)
#   MUSE_BIND_KEYS=1            Bind Ctrl+B / ESC-M shortcuts (default 1)
#   MUSE_POST_COMMIT_CMD        Shell cmd run after each muse commit
#   MUSE_POST_CHECKOUT_CMD      Shell cmd run after each muse checkout
#   MUSE_POST_MERGE_CMD         Shell cmd run after a clean muse merge
# ==============================================================================

# Require ZSH 5.0+ (associative arrays, autoload -Uz, EPOCHSECONDS)
autoload -Uz is-at-least
if ! is-at-least 5.0; then
  print -P "%F{red}[muse] ZSH 5.0+ required (you have $ZSH_VERSION). Plugin not loaded.%f" >&2
  return 1
fi

# Muse requires python3; so does this plugin (JSON/TOML parsing, one subprocess
# per prompt refresh — never on every keystroke).
if ! command -v python3 >/dev/null 2>&1; then
  print -P "%F{red}[muse] python3 not found in PATH. Plugin not loaded.%f" >&2
  return 1
fi

# Load datetime module for EPOCHREALTIME (millisecond session timestamps).
# zsh/datetime is standard since ZSH 4.3. Failure is silently ignored.
zmodload -i zsh/datetime 2>/dev/null

# ── §0  CONFIGURATION ─────────────────────────────────────────────────────────
# The := operator sets the variable only if it is unset or empty, so users can
# override any of these in their .zshrc before sourcing the plugin.

: ${MUSE_PROMPT_SHOW_DOMAIN:=1}
: ${MUSE_PROMPT_SHOW_OPS:=1}
: ${MUSE_PROMPT_ICONS:=1}
: ${MUSE_AGENT_MODE:=0}
: ${MUSE_DIRTY_TIMEOUT:=1}
: ${MUSE_SESSION_LOG_DIR:="$HOME/.muse/sessions"}
: ${MUSE_BIND_KEYS:=1}
: ${MUSE_POST_COMMIT_CMD:=}
: ${MUSE_POST_CHECKOUT_CMD:=}
: ${MUSE_POST_MERGE_CMD:=}

# Domain icon map — reassign individual elements before sourcing to override.
typeset -gA MUSE_DOMAIN_ICONS
MUSE_DOMAIN_ICONS=(
  midi      "♪"
  code      "⌥"
  bitcoin   "₿"
  scaffold  "⬡"
  genomics  "🧬"
  spatial   "◉"
  _default  "◈"
)

# Domain prompt-colour map (ZSH %F{…} codes).
typeset -gA MUSE_DOMAIN_COLORS
MUSE_DOMAIN_COLORS=(
  midi      "%F{magenta}"
  code      "%F{cyan}"
  bitcoin   "%F{yellow}"
  scaffold  "%F{blue}"
  genomics  "%F{green}"
  spatial   "%F{white}"
  _default  "%F{white}"
)

# ── §1  INTERNAL STATE ────────────────────────────────────────────────────────
# Exported vars ($MUSE_*) are visible to subprocesses — agents read these.
# Private vars ($_MUSE_*) control plugin-internal caching behaviour.

typeset -g   MUSE_REPO_ROOT=""          # absolute path to repo containing .muse/
typeset -g   MUSE_DOMAIN="midi"         # active domain plugin name
typeset -g   MUSE_BRANCH=""             # current branch, or 8-char SHA if detached
typeset -gi  MUSE_DETACHED=0            # 1 when HEAD is a detached SHA
typeset -gi  MUSE_DIRTY=0               # 1 when working tree has uncommitted changes
typeset -gi  MUSE_DIRTY_COUNT=0         # number of changed paths
typeset -g   MUSE_DIRTY_STATE=""        # "clean" | "dirty" | "?" (timeout)
typeset -gi  MUSE_MERGING=0             # 1 when MERGE_STATE.json exists
typeset -g   MUSE_MERGE_BRANCH=""       # branch being merged in
typeset -gi  MUSE_CONFLICT_COUNT=0      # paths with unresolved conflicts
typeset -g   MUSE_USER_TYPE="human"     # "human" | "agent" (from config.toml)
typeset -g   MUSE_LAST_SEMVER=""        # sem_ver_bump of the HEAD commit

typeset -gi  _MUSE_CACHE_VALID=0        # 0 = cache needs refresh
typeset -gi  _MUSE_CMD_RAN=0            # 1 after any muse command is run
typeset -gF  _MUSE_CMD_START=0.0        # EPOCHREALTIME when last muse cmd started
typeset -g   _MUSE_LAST_CMD=""          # full text of the last muse command

# Always exported — the machine-readable repo snapshot for AI agents.
export MUSE_CONTEXT_JSON=""

# Agent session identity — set by muse-agent-session, cleared by muse-agent-end.
export MUSE_SESSION_MODEL_ID=""
export MUSE_SESSION_AGENT_ID=""
export MUSE_SESSION_START=""
export MUSE_SESSION_LOG_FILE=""

# ── §2  CORE DETECTION ────────────────────────────────────────────────────────

# Walk up from $PWD to find the .muse/ directory. Sets MUSE_REPO_ROOT.
# Pure ZSH — zero subprocesses. Returns 1 if not inside a muse repo.
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

# Read branch from .muse/HEAD without forking.
# Sets MUSE_BRANCH (branch name or 8-char SHA) and MUSE_DETACHED.
function _muse_parse_head() {
  local head_file="$MUSE_REPO_ROOT/.muse/HEAD"
  if [[ ! -f "$head_file" ]]; then
    MUSE_BRANCH=""; MUSE_DETACHED=0; return 1
  fi
  local raw
  raw=$(<"$head_file")
  if [[ "$raw" == "ref: refs/heads/"* ]]; then
    MUSE_BRANCH="${raw#ref: refs/heads/}"
    MUSE_DETACHED=0
  else
    # Detached HEAD — show short SHA
    MUSE_BRANCH="${raw:0:8}"
    MUSE_DETACHED=1
  fi
}

# Read domain, user type, and merge state in one python3 invocation.
# Uses an environment variable to pass the repo root safely (handles spaces).
# Sets MUSE_DOMAIN, MUSE_USER_TYPE, MUSE_MERGING, MUSE_MERGE_BRANCH,
# MUSE_CONFLICT_COUNT.
function _muse_parse_meta() {
  local output
  output=$(MUSE_META_ROOT="$MUSE_REPO_ROOT" python3 <<'PYEOF' 2>/dev/null
import json, os, re, sys

root = os.environ.get('MUSE_META_ROOT', '')

# ── domain from repo.json ────────────────────────────────────────────────────
domain = 'midi'
try:
    rj = json.load(open(os.path.join(root, '.muse', 'repo.json')))
    domain = rj.get('domain', 'midi')
except Exception:
    pass

# ── user.type from config.toml (stdlib tomllib, Python ≥ 3.11) ──────────────
user_type = 'human'
cfg_path = os.path.join(root, '.muse', 'config.toml')
if os.path.exists(cfg_path):
    try:
        import tomllib
        with open(cfg_path, 'rb') as f:
            cfg = tomllib.load(f)
        user_type = cfg.get('user', {}).get('type', 'human')
    except ImportError:
        # Regex fallback for Python < 3.11 (edge case — Muse requires 3.14)
        m = re.search(r'type\s*=\s*"(\w+)"', open(cfg_path).read())
        if m:
            user_type = m.group(1)
    except Exception:
        pass

# ── merge state from MERGE_STATE.json ────────────────────────────────────────
merging, merge_branch, conflict_count = 0, '', 0
merge_path = os.path.join(root, '.muse', 'MERGE_STATE.json')
if os.path.exists(merge_path):
    try:
        ms = json.load(open(merge_path))
        merging = 1
        merge_branch = ms.get('other_branch') or ms.get('theirs_commit', '')[:8]
        conflict_count = len(ms.get('conflict_paths', []))
    except Exception:
        merging = 1  # file exists but unreadable — still in a merge

# Unit separator (0x1F) avoids clashes with any reasonable field value.
sep = '\x1f'
print(f'{domain}{sep}{user_type}{sep}{merging}{sep}{merge_branch}{sep}{conflict_count}')
PYEOF
  )
  if [[ -z "$output" ]]; then
    MUSE_DOMAIN="midi"; MUSE_USER_TYPE="human"
    MUSE_MERGING=0; MUSE_MERGE_BRANCH=""; MUSE_CONFLICT_COUNT=0
    return 1
  fi
  IFS=$'\x1f' read -r MUSE_DOMAIN MUSE_USER_TYPE MUSE_MERGING \
                       MUSE_MERGE_BRANCH MUSE_CONFLICT_COUNT <<< "$output"
  : ${MUSE_DOMAIN:=midi}
  : ${MUSE_USER_TYPE:=human}
  : ${MUSE_MERGING:=0}
  : ${MUSE_CONFLICT_COUNT:=0}
}

# Run muse status --porcelain with a timeout. Counts changed paths.
# Sets MUSE_DIRTY, MUSE_DIRTY_COUNT, MUSE_DIRTY_STATE.
function _muse_check_dirty() {
  local output rc count=0
  output=$(cd "$MUSE_REPO_ROOT" && \
           timeout "${MUSE_DIRTY_TIMEOUT}" muse status --porcelain 2>/dev/null)
  rc=$?
  if (( rc == 124 )); then
    # timeout — show "?" in prompt rather than hanging
    MUSE_DIRTY=0; MUSE_DIRTY_COUNT=0; MUSE_DIRTY_STATE="?"
    return
  fi
  while IFS= read -r line; do
    [[ "$line" == "##"* || -z "$line" ]] && continue
    (( count++ ))
  done <<< "$output"
  if (( count > 0 )); then
    MUSE_DIRTY=1; MUSE_DIRTY_COUNT=$count; MUSE_DIRTY_STATE="dirty"
  else
    MUSE_DIRTY=0; MUSE_DIRTY_COUNT=0; MUSE_DIRTY_STATE="clean"
  fi
}

# Read sem_ver_bump from the HEAD commit record without forking.
# Sets MUSE_LAST_SEMVER ("major" | "minor" | "patch" | "").
function _muse_parse_semver() {
  MUSE_LAST_SEMVER=""
  [[ -z "$MUSE_BRANCH" || $MUSE_DETACHED -eq 1 ]] && return
  local branch_file="$MUSE_REPO_ROOT/.muse/refs/heads/$MUSE_BRANCH"
  [[ ! -f "$branch_file" ]] && return
  local commit_id
  commit_id=$(<"$branch_file")
  local commit_file="$MUSE_REPO_ROOT/.muse/commits/${commit_id}.json"
  [[ ! -f "$commit_file" ]] && return
  MUSE_LAST_SEMVER=$(MUSE_META_CFILE="$commit_file" python3 <<'PYEOF' 2>/dev/null
import json, os
try:
    d = json.load(open(os.environ['MUSE_META_CFILE']))
    v = d.get('sem_ver_bump', 'none')
    print('' if v == 'none' else v)
except Exception:
    print('')
PYEOF
  )
}

# Build the compact $MUSE_CONTEXT_JSON env var for agent consumption.
# Runs python3 to ensure correct JSON encoding of all values.
function _muse_build_context_json() {
  MUSE_CONTEXT_JSON=$(python3 -c "
import json
ctx = {
    'schema_version': 1,
    'domain':         '${MUSE_DOMAIN}',
    'branch':         '${MUSE_BRANCH}',
    'repo_root':      '${MUSE_REPO_ROOT//\'/\\'\\'}',
    'dirty':          bool(${MUSE_DIRTY:-0}),
    'dirty_count':    ${MUSE_DIRTY_COUNT:-0},
    'merging':        bool(${MUSE_MERGING:-0}),
    'merge_branch':   '${MUSE_MERGE_BRANCH}' or None,
    'conflict_count': ${MUSE_CONFLICT_COUNT:-0},
    'user_type':      '${MUSE_USER_TYPE:-human}',
    'agent_id':       '${MUSE_SESSION_AGENT_ID}' or None,
    'model_id':       '${MUSE_SESSION_MODEL_ID}' or None,
    'semver':         '${MUSE_LAST_SEMVER}' or None,
}
print(json.dumps(ctx, separators=(',', ':')))
" 2>/dev/null)
  export MUSE_CONTEXT_JSON
}

# ── §3  CACHE MANAGEMENT ──────────────────────────────────────────────────────

# Full refresh — includes dirty check. Called after muse commands and on cold
# cache entry. Clears all MUSE_* vars on non-repo directories.
function _muse_refresh() {
  if ! _muse_find_root; then
    MUSE_DOMAIN=""; MUSE_BRANCH=""
    MUSE_DIRTY=0; MUSE_DIRTY_COUNT=0; MUSE_DIRTY_STATE=""
    MUSE_MERGING=0; MUSE_MERGE_BRANCH=""; MUSE_CONFLICT_COUNT=0
    export MUSE_CONTEXT_JSON=""
    _MUSE_CACHE_VALID=1
    _MUSE_CMD_RAN=0
    return 1
  fi
  _muse_parse_head
  _muse_parse_meta
  _muse_check_dirty
  _muse_parse_semver
  _muse_build_context_json
  _MUSE_CACHE_VALID=1
  _MUSE_CMD_RAN=0
}

# Fast refresh — skips dirty check. Used on directory change and for p10k
# instant prompt where blocking the shell for a second is unacceptable.
function _muse_refresh_fast() {
  if ! _muse_find_root; then
    MUSE_DOMAIN=""; MUSE_BRANCH=""; export MUSE_CONTEXT_JSON=""
    _MUSE_CACHE_VALID=1
    return 1
  fi
  _muse_parse_head
  _muse_parse_meta
  _muse_build_context_json
  _MUSE_CACHE_VALID=1
}

# ── §4  ZSH HOOKS ─────────────────────────────────────────────────────────────

# Invalidate the entire cache when changing directories. The fast refresh runs
# immediately so the next prompt shows the new repo's state without a delay.
function _muse_hook_chpwd() {
  _MUSE_CACHE_VALID=0
  _MUSE_CMD_RAN=0
  MUSE_REPO_ROOT=""; MUSE_BRANCH=""
  MUSE_DIRTY=0; MUSE_DIRTY_COUNT=0; MUSE_DIRTY_STATE=""
  MUSE_MERGING=0; MUSE_MERGE_BRANCH=""; MUSE_CONFLICT_COUNT=0
  export MUSE_CONTEXT_JSON=""
  _muse_refresh_fast 2>/dev/null
}
chpwd_functions+=(_muse_hook_chpwd)

# Track when a muse (or aliased) command is about to run. Records timing for
# session logs and sets the refresh flag so the next prompt reflects changes.
function _muse_hook_preexec() {
  local first_word="${${(z)1}[1]}"
  # Match the raw muse binary and all m-prefixed aliases that wrap muse.
  if [[ "$first_word" == "muse" || "$first_word" == m[a-z]* ]]; then
    _MUSE_CMD_RAN=1
    _MUSE_CMD_START=${EPOCHREALTIME:-0}
    _MUSE_LAST_CMD="$1"
    _muse_session_log_start "$1"
  fi
}
preexec_functions+=(_muse_hook_preexec)

# Before each prompt: flush the session log entry, refresh the cache when
# needed, and emit $MUSE_CONTEXT_JSON to stderr in agent mode.
function _muse_hook_precmd() {
  if (( _MUSE_CMD_START > 0 )); then
    local epoch_now=${EPOCHREALTIME:-0}
    local elapsed_ms=$(( int(($epoch_now - _MUSE_CMD_START) * 1000) ))
    _muse_session_log_end $? $elapsed_ms
    _MUSE_CMD_START=0.0
    _muse_run_post_hooks
  fi

  if (( _MUSE_CACHE_VALID == 0 )); then
    _muse_refresh 2>/dev/null
  elif (( _MUSE_CMD_RAN )); then
    _muse_refresh 2>/dev/null
  fi

  # In agent mode, always broadcast state to stderr for orchestrating processes.
  if [[ "$MUSE_AGENT_MODE" == "1" && -n "$MUSE_REPO_ROOT" ]]; then
    print -r -- "$MUSE_CONTEXT_JSON" >&2
  fi
}
precmd_functions+=(_muse_hook_precmd)

# ── §5  PROMPT FUNCTIONS ──────────────────────────────────────────────────────

# Primary left-prompt segment. Add $(muse_prompt_info) to your $PROMPT, or
# set POWERLEVEL9K_LEFT_PROMPT_ELEMENTS=(… muse_vcs …) for p10k.
# Emits nothing when not in a muse repo.
function muse_prompt_info() {
  [[ -z "$MUSE_REPO_ROOT" ]] && return

  if [[ "$MUSE_AGENT_MODE" == "1" ]]; then
    _muse_prompt_machine
    return
  fi

  local domain="${MUSE_DOMAIN:-?}"
  local icon color
  icon="${MUSE_DOMAIN_ICONS[$domain]:-${MUSE_DOMAIN_ICONS[_default]}}"
  color="${MUSE_DOMAIN_COLORS[$domain]:-${MUSE_DOMAIN_COLORS[_default]}}"
  [[ "$MUSE_PROMPT_ICONS" == "0" ]] && icon="[${domain}]"

  local branch_display="$MUSE_BRANCH"
  (( MUSE_DETACHED )) && branch_display="(detached:${MUSE_BRANCH})"

  # ── Dirty indicator ────────────────────────────────────────────────────────
  local dirty=""
  case "$MUSE_DIRTY_STATE" in
    "?")
      dirty=" %F{yellow}?%f"
      ;;
    dirty)
      if [[ "$MUSE_PROMPT_SHOW_OPS" == "1" && $MUSE_DIRTY_COUNT -gt 0 ]]; then
        dirty=" %F{red}✗%f %F{white}${MUSE_DIRTY_COUNT}Δ%f"
      else
        dirty=" %F{red}✗%f"
      fi
      ;;
    clean)
      dirty=" %F{green}✓%f"
      ;;
  esac

  # ── Merge indicator ────────────────────────────────────────────────────────
  local merge=""
  if (( MUSE_MERGING )); then
    local cfl=""
    if (( MUSE_CONFLICT_COUNT == 1 )); then
      cfl=" (1 conflict)"
    elif (( MUSE_CONFLICT_COUNT > 1 )); then
      cfl=" (${MUSE_CONFLICT_COUNT} conflicts)"
    fi
    merge=" %F{yellow}⚡ ←%f %F{magenta}${MUSE_MERGE_BRANCH}${cfl}%f"
  fi

  # ── Agent badge ───────────────────────────────────────────────────────────
  local agent=""
  if [[ -n "$MUSE_SESSION_MODEL_ID" ]]; then
    agent=" %F{blue}[🤖 ${MUSE_SESSION_MODEL_ID}]%f"
  elif [[ "$MUSE_USER_TYPE" == "agent" ]]; then
    agent=" %F{blue}[agent]%f"
  fi

  # ── Domain label (optional) ───────────────────────────────────────────────
  local domain_label=""
  [[ "$MUSE_PROMPT_SHOW_DOMAIN" == "1" ]] && domain_label="${domain}:"

  echo -n "${color}${icon} ${domain_label}${branch_display}%f${dirty}${merge}${agent}"
}

# Right-prompt segment: shows the SemVer bump of the HEAD commit.
# Add $(muse_rprompt_info) to $RPROMPT or POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS.
function muse_rprompt_info() {
  [[ -z "$MUSE_REPO_ROOT" || -z "$MUSE_LAST_SEMVER" ]] && return
  [[ "$MUSE_AGENT_MODE" == "1" ]] && return
  local color="%F{green}"
  case "$MUSE_LAST_SEMVER" in
    major) color="%F{red}" ;;
    minor) color="%F{yellow}" ;;
  esac
  echo -n "${color}[${MUSE_LAST_SEMVER:u}]%f"
}

# Machine-readable prompt for MUSE_AGENT_MODE=1.
# Format: [domain|branch|clean/dirty:N|no-merge/merging:branch:N|agent:id]
function _muse_prompt_machine() {
  local dirty_part="clean"
  (( MUSE_DIRTY )) && dirty_part="dirty:${MUSE_DIRTY_COUNT}"
  [[ "$MUSE_DIRTY_STATE" == "?" ]] && dirty_part="unknown"

  local merge_part="no-merge"
  (( MUSE_MERGING )) && \
    merge_part="merging:${MUSE_MERGE_BRANCH}:${MUSE_CONFLICT_COUNT}"

  local agent_part=""
  [[ -n "$MUSE_SESSION_AGENT_ID" ]] && agent_part="|agent:${MUSE_SESSION_AGENT_ID}"

  echo -n "[${MUSE_DOMAIN}|${MUSE_BRANCH}|${dirty_part}|${merge_part}${agent_part}]"
}

# ── §6  ALIASES ───────────────────────────────────────────────────────────────

# Core VCS
alias mst='muse status'
alias msts='muse status --short'
alias mstp='muse status --porcelain'
alias mstb='muse status --branch'
alias mcm='muse commit -m'
alias mco='muse checkout'
alias mlg='muse log'
alias mlgo='muse log --oneline'
alias mlgg='muse log --graph'
alias mlggs='muse log --graph --oneline'
alias mdf='muse diff'
alias mdfst='muse diff --stat'
alias mdfp='muse diff --patch'
alias mbr='muse branch'
alias mbrv='muse branch -v'
alias msh='muse show'
alias mbl='muse blame'
alias mrl='muse reflog'
alias mbs='muse bisect'

# Stash
alias msta='muse stash'
alias mstap='muse stash pop'
alias mstal='muse stash list'
alias mstad='muse stash drop'

# Tags
alias mtg='muse tag'

# Remote / networking
alias mfh='muse fetch'
alias mpull='muse pull'   # mpull not mpl — avoids collision with muse plumbing
alias mpush='muse push'
alias mrm='muse remote'
alias mclone='muse clone'

# Worktree / workspace
alias mwt='muse worktree'
alias mwsp='muse workspace'

# Domain shortcuts
alias mmidi='muse midi'
alias mcode='muse code'
alias mcoord='muse coord'
alias mplumb='muse plumbing'

# Config / hub
alias mcfg='muse config'
alias mcfgs='muse config show'
alias mhub='muse hub'

# Misc porcelain
alias mchp='muse cherry-pick'
alias mrst='muse reset'
alias mrvt='muse revert'
alias mgc='muse gc'
alias mcheck='muse check'
alias mdoms='muse domains'
alias mannot='muse annotate'
alias mattr='muse attributes'

# ── §7  WORKFLOW FUNCTIONS ────────────────────────────────────────────────────

# Create and switch to feat/<name>.
function muse-new-feat() {
  local name="${1:?Usage: muse-new-feat <name>}"
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  muse branch "feat/${name}" && muse checkout "feat/${name}"
}

# Create and switch to fix/<name>.
function muse-new-fix() {
  local name="${1:?Usage: muse-new-fix <name>}"
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  muse branch "fix/${name}" && muse checkout "fix/${name}"
}

# Create and switch to refactor/<name>.
function muse-new-refactor() {
  local name="${1:?Usage: muse-new-refactor <name>}"
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  muse branch "refactor/${name}" && muse checkout "refactor/${name}"
}

# Commit with an auto-timestamped WIP message.
function muse-wip() {
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  muse commit -m "[WIP] $(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

# Fetch + pull + status in one go.
function muse-sync() {
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  muse fetch && muse pull && muse status
}

# Merge with structured conflict reporting and optional editor launch.
function muse-safe-merge() {
  local branch="${1:?Usage: muse-safe-merge <branch>}"
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }

  muse merge "$branch"
  local rc=$?

  if (( rc != 0 )) && [[ -f "$MUSE_REPO_ROOT/.muse/MERGE_STATE.json" ]]; then
    echo ""
    echo "Conflicts detected:"
    MUSE_META_ROOT="$MUSE_REPO_ROOT" python3 <<'PYEOF' 2>/dev/null
import json, os
root = os.environ['MUSE_META_ROOT']
d = json.load(open(os.path.join(root, '.muse', 'MERGE_STATE.json')))
other = d.get('other_branch', 'unknown')
for p in d.get('conflict_paths', []):
    print(f'  ✗  {p}')
print(f"\nResolve conflicts, then: muse commit -m \"Merge {other}\"")
PYEOF
    if [[ -n "${EDITOR:-}" ]]; then
      local conflict_paths
      conflict_paths=$(MUSE_META_ROOT="$MUSE_REPO_ROOT" python3 <<'PYEOF' 2>/dev/null
import json, os
root = os.environ['MUSE_META_ROOT']
d = json.load(open(os.path.join(root, '.muse', 'MERGE_STATE.json')))
for p in d.get('conflict_paths', []):
    print(p)
PYEOF
      )
      if [[ -n "$conflict_paths" ]]; then
        echo ""
        echo "Opening conflicts in $EDITOR..."
        local paths_array=("${(f)conflict_paths}")
        "$EDITOR" "${paths_array[@]}"
      fi
    fi
  fi
  return $rc
}

# Interactive guided commit with domain-aware metadata prompts.
function muse-quick-commit() {
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }

  local message
  print -n "Commit message: "
  read -r message
  [[ -z "$message" ]] && { echo "Aborted." >&2; return 1; }

  local -a meta_args
  case "$MUSE_DOMAIN" in
    midi)
      local section track emotion
      print -n "Section (verse/chorus/bridge, blank to skip): "; read -r section
      [[ -n "$section" ]] && meta_args+=("--meta" "section=$section")
      print -n "Track name (blank to skip): "; read -r track
      [[ -n "$track" ]] && meta_args+=("--meta" "track=$track")
      print -n "Emotion (blank to skip): "; read -r emotion
      [[ -n "$emotion" ]] && meta_args+=("--meta" "emotion=$emotion")
      ;;
    code)
      local module breaking
      print -n "Module/package (blank to skip): "; read -r module
      [[ -n "$module" ]] && meta_args+=("--meta" "module=$module")
      print -n "Breaking change? (y/N): "; read -r breaking
      [[ "$breaking" == [Yy]* ]] && meta_args+=("--meta" "breaking=true")
      ;;
  esac

  [[ -n "$MUSE_SESSION_AGENT_ID"  ]] && meta_args+=("--agent-id"  "$MUSE_SESSION_AGENT_ID")
  [[ -n "$MUSE_SESSION_MODEL_ID"  ]] && meta_args+=("--model-id"  "$MUSE_SESSION_MODEL_ID")

  muse commit -m "$message" "${meta_args[@]}"
}

# Repo health summary: dirty state, merge, stashes, remotes.
function muse-health() {
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  local icon="${MUSE_DOMAIN_ICONS[$MUSE_DOMAIN]:-◈}"
  echo ""
  echo "  ${icon} MUSE REPO HEALTH — ${MUSE_DOMAIN}:${MUSE_BRANCH}"
  echo "  ──────────────────────────────────────────────"

  if (( MUSE_DIRTY )); then
    echo "  Working tree  ✗  ${MUSE_DIRTY_COUNT} changed path(s)"
  elif [[ "$MUSE_DIRTY_STATE" == "?" ]]; then
    echo "  Working tree  ?  (check timed out)"
  else
    echo "  Working tree  ✓  clean"
  fi

  if (( MUSE_MERGING )); then
    echo "  Merge state   ⚡  in progress ← ${MUSE_MERGE_BRANCH} (${MUSE_CONFLICT_COUNT} conflict(s))"
  else
    echo "  Merge state   ✓  none"
  fi

  local stash_count=0
  stash_count=$(cd "$MUSE_REPO_ROOT" && muse stash list 2>/dev/null | wc -l | tr -d ' ')
  if (( stash_count > 0 )); then
    echo "  Stashes       ⚠  ${stash_count} pending"
  else
    echo "  Stashes       ✓  none"
  fi

  local -a remotes=()
  [[ -d "$MUSE_REPO_ROOT/.muse/remotes" ]] && \
    remotes=($(ls "$MUSE_REPO_ROOT/.muse/remotes/" 2>/dev/null))
  if (( ${#remotes[@]} > 0 )); then
    echo "  Remotes       ✓  ${remotes[*]}"
  else
    echo "  Remotes       —  none configured"
  fi

  echo "  Domain        ✓  ${MUSE_DOMAIN}"
  echo "  User type     ✓  ${MUSE_USER_TYPE}"
  [[ -n "$MUSE_SESSION_MODEL_ID" ]] && \
    echo "  Agent session ✓  ${MUSE_SESSION_MODEL_ID} / ${MUSE_SESSION_AGENT_ID}"
  echo ""
}

# Show authorship provenance of the HEAD commit (human vs agent).
function muse-who-last() {
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  [[ -z "$MUSE_BRANCH" || $MUSE_DETACHED -eq 1 ]] && \
    { echo "No named branch — detached HEAD." >&2; return 1; }

  local branch_file="$MUSE_REPO_ROOT/.muse/refs/heads/$MUSE_BRANCH"
  [[ ! -f "$branch_file" ]] && { echo "Branch ref not found." >&2; return 1; }

  local commit_id
  commit_id=$(<"$branch_file")
  local commit_file="$MUSE_REPO_ROOT/.muse/commits/${commit_id}.json"
  [[ ! -f "$commit_file" ]] && { echo "Commit record not found." >&2; return 1; }

  MUSE_META_CFILE="$commit_file" python3 <<'PYEOF' 2>/dev/null
import json, os
d = json.load(open(os.environ['MUSE_META_CFILE']))
sha      = d.get('commit_id', '')[:8]
author   = d.get('author', 'unknown')
agent_id = d.get('agent_id', '')
model_id = d.get('model_id', '')
msg      = d.get('message', '')[:72]
dt       = d.get('committed_at', '')[:10]
semver   = d.get('sem_ver_bump', 'none')
breaking = d.get('breaking_changes', [])

print(f'  Last commit:  {sha}  ({dt})')
print(f'  Message:      "{msg}"')
if agent_id or model_id:
    print(f'  Author:       {author}  [AGENT]')
    if model_id: print(f'  Model:        {model_id}')
    if agent_id: print(f'  Agent ID:     {agent_id}')
else:
    print(f'  Author:       {author}  [human]')
if semver and semver != 'none':
    print(f'  SemVer:       {semver.upper()}')
if breaking:
    for b in breaking:
        print(f'  Breaking:     {b}')
PYEOF
}

# Scan the last N commits and show who made them (human vs agent + model).
function muse-agent-blame() {
  local n="${1:-10}"
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  [[ -z "$MUSE_BRANCH" ]] && { echo "No branch." >&2; return 1; }

  MUSE_META_ROOT="$MUSE_REPO_ROOT" MUSE_META_BRANCH="$MUSE_BRANCH" \
  MUSE_META_N="$n" python3 <<'PYEOF' 2>/dev/null
import json, os, sys

root   = os.environ['MUSE_META_ROOT']
branch = os.environ['MUSE_META_BRANCH']
n      = int(os.environ['MUSE_META_N'])

branch_file = os.path.join(root, '.muse', 'refs', 'heads', branch)
if not os.path.exists(branch_file):
    print("Branch ref not found.", file=sys.stderr)
    sys.exit(1)

commit_id = open(branch_file).read().strip()
seen, count = set(), 0

print(f"\n  Agent provenance — last {n} commits on {branch}")
print(f"  {'SHA':8}  {'Date':10}  {'Type':6}  {'Author/Model':30}  Message")
print(f"  {'─'*8}  {'─'*10}  {'─'*6}  {'─'*30}  {'─'*30}")

while commit_id and count < n:
    if commit_id in seen:
        break
    seen.add(commit_id)
    cfile = os.path.join(root, '.muse', 'commits', f'{commit_id}.json')
    if not os.path.exists(cfile):
        break
    d = json.load(open(cfile))
    sha      = commit_id[:8]
    date     = d.get('committed_at', '')[:10]
    author   = d.get('author', '?')
    agent_id = d.get('agent_id', '')
    model_id = d.get('model_id', '')
    msg      = d.get('message', '')[:30]
    kind     = 'agent' if (agent_id or model_id) else 'human'
    who      = (model_id or agent_id or author)[:30]
    print(f"  {sha}  {date}  {kind:6}  {who:30}  {msg}")
    commit_id = d.get('parent_commit_id') or ''
    count += 1
print()
PYEOF
}

# ── §8  AGENT-NATIVE FUNCTIONS ────────────────────────────────────────────────

# Output a compact, token-efficient repo context block for AI agent consumption.
# Flags: --json  structured JSON  |  --toml  TOML  |  --oneline  single line
function muse-context() {
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }

  local fmt="human" oneline=0
  for arg in "$@"; do
    case "$arg" in
      --json)    fmt="json"   ;;
      --toml)    fmt="toml"   ;;
      --oneline) oneline=1    ;;
    esac
  done

  # Gather HEAD commit info via a single python3 call.
  local commit_id="" commit_msg="" commit_date="" commit_author=""
  local commit_agent_id="" commit_model_id="" commit_semver=""
  if [[ -n "$MUSE_BRANCH" && $MUSE_DETACHED -eq 0 ]]; then
    local branch_file="$MUSE_REPO_ROOT/.muse/refs/heads/$MUSE_BRANCH"
    if [[ -f "$branch_file" ]]; then
      commit_id=$(<"$branch_file")
      local cfile="$MUSE_REPO_ROOT/.muse/commits/${commit_id}.json"
      if [[ -f "$cfile" ]]; then
        local raw_info
        raw_info=$(MUSE_META_CFILE="$cfile" python3 <<'PYEOF' 2>/dev/null
import json, os
sep = '\x1f'
try:
    d = json.load(open(os.environ['MUSE_META_CFILE']))
    fields = [
        d.get('message', '')[:60].replace('\n', ' '),
        d.get('committed_at', '')[:10],
        d.get('author', ''),
        d.get('agent_id', ''),
        d.get('model_id', ''),
        d.get('sem_ver_bump', 'none'),
    ]
    print(sep.join(fields))
except Exception:
    print(sep * 5)
PYEOF
        )
        IFS=$'\x1f' read -r commit_msg commit_date commit_author \
                         commit_agent_id commit_model_id commit_semver \
                         <<< "$raw_info"
      fi
    fi
  fi

  local stash_count=0
  stash_count=$(cd "$MUSE_REPO_ROOT" && muse stash list 2>/dev/null | wc -l | tr -d ' ')

  local -a remotes=()
  [[ -d "$MUSE_REPO_ROOT/.muse/remotes" ]] && \
    remotes=($(ls "$MUSE_REPO_ROOT/.muse/remotes/" 2>/dev/null))

  # ── Output modes ────────────────────────────────────────────────────────────
  if [[ $oneline -eq 1 ]]; then
    local dirty_label="clean"
    (( MUSE_DIRTY )) && dirty_label="dirty:${MUSE_DIRTY_COUNT}"
    echo "${MUSE_DOMAIN}:${MUSE_BRANCH} ${dirty_label} commit:${commit_id:0:8}"
    return
  fi

  if [[ "$fmt" == "json" ]]; then
    echo "$MUSE_CONTEXT_JSON" | python3 -c \
      "import json,sys; print(json.dumps(json.load(sys.stdin), indent=2))" 2>/dev/null
    return
  fi

  if [[ "$fmt" == "toml" ]]; then
    echo "[repo]"
    echo "domain = \"$MUSE_DOMAIN\""
    echo "branch = \"$MUSE_BRANCH\""
    echo "dirty = $(( MUSE_DIRTY ))"
    echo "dirty_count = $MUSE_DIRTY_COUNT"
    echo "merging = $(( MUSE_MERGING ))"
    [[ -n "$MUSE_MERGE_BRANCH" ]] && echo "merge_branch = \"$MUSE_MERGE_BRANCH\""
    echo "conflict_count = $MUSE_CONFLICT_COUNT"
    echo ""
    echo "[commit]"
    echo "id = \"${commit_id:0:8}\""
    echo "message = \"$commit_msg\""
    echo "date = \"$commit_date\""
    echo "author = \"$commit_author\""
    [[ -n "$commit_agent_id" ]] && echo "agent_id = \"$commit_agent_id\""
    [[ -n "$commit_model_id" ]] && echo "model_id = \"$commit_model_id\""
    echo ""
    echo "[session]"
    echo "user_type = \"$MUSE_USER_TYPE\""
    [[ -n "$MUSE_SESSION_AGENT_ID" ]] && echo "agent_id = \"$MUSE_SESSION_AGENT_ID\""
    [[ -n "$MUSE_SESSION_MODEL_ID" ]] && echo "model_id = \"$MUSE_SESSION_MODEL_ID\""
    return
  fi

  # Human format — dense, minimal whitespace, designed for AI context injection.
  local icon="${MUSE_DOMAIN_ICONS[$MUSE_DOMAIN]:-◈}"
  echo ""
  echo "  MUSE REPO CONTEXT  ${icon} ${MUSE_DOMAIN}:${MUSE_BRANCH}"
  echo "  ──────────────────────────────────────────────────────"
  echo "  domain      $MUSE_DOMAIN"
  echo "  branch      $MUSE_BRANCH"
  if [[ -n "$commit_id" ]]; then
    echo "  commit      ${commit_id:0:8}  \"$commit_msg\"  ($commit_date)"
    if [[ -n "$commit_agent_id" || -n "$commit_model_id" ]]; then
      echo "  last author ${commit_author} [agent: ${commit_model_id:-${commit_agent_id}}]"
    else
      echo "  last author $commit_author [human]"
    fi
    [[ -n "$commit_semver" && "$commit_semver" != "none" ]] && \
      echo "  semver      ${commit_semver:u}"
  fi
  if (( MUSE_DIRTY )); then
    echo "  dirty       yes — ${MUSE_DIRTY_COUNT} changed"
  else
    echo "  dirty       no"
  fi
  if (( MUSE_MERGING )); then
    echo "  merging     yes ← $MUSE_MERGE_BRANCH (${MUSE_CONFLICT_COUNT} conflicts)"
  else
    echo "  merging     no"
  fi
  (( stash_count > 0 )) && echo "  stashes     $stash_count"
  (( ${#remotes[@]} > 0 )) && echo "  remotes     ${remotes[*]}"
  echo "  user        $MUSE_USER_TYPE"
  [[ -n "$MUSE_SESSION_AGENT_ID" ]] && echo "  agent       $MUSE_SESSION_AGENT_ID"
  [[ -n "$MUSE_SESSION_MODEL_ID" ]] && echo "  model       $MUSE_SESSION_MODEL_ID"
  echo ""
}

# Begin an agent session. Sets identity env vars and starts a JSONL session log.
# Usage: muse-agent-session <model_id> [agent_id]
function muse-agent-session() {
  local model_id="${1:?Usage: muse-agent-session <model_id> [agent_id]}"
  local agent_id="${2:-agent-$$}"

  export MUSE_SESSION_MODEL_ID="$model_id"
  export MUSE_SESSION_AGENT_ID="$agent_id"
  export MUSE_SESSION_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  mkdir -p "$MUSE_SESSION_LOG_DIR"
  local log_file="$MUSE_SESSION_LOG_DIR/$(date -u +%Y%m%d-%H%M%S)-$$.jsonl"
  export MUSE_SESSION_LOG_FILE="$log_file"

  # Write session-start entry with pure ZSH printf (no subprocess).
  printf '{"t":"%s","event":"session_start","model_id":"%s","agent_id":"%s","domain":"%s","branch":"%s","repo_root":"%s","pid":%d}\n' \
    "$MUSE_SESSION_START" "$model_id" "$agent_id" \
    "${MUSE_DOMAIN:-}" "${MUSE_BRANCH:-}" "${MUSE_REPO_ROOT//\"/\\\"}" \
    $$ >> "$log_file"

  echo "  Agent session started"
  echo "  model    $model_id"
  echo "  agent    $agent_id"
  echo "  log      $log_file"
  _muse_refresh 2>/dev/null
}

# End the current agent session, write a summary entry, unset identity vars.
function muse-agent-end() {
  if [[ -z "${MUSE_SESSION_MODEL_ID}" ]]; then
    echo "No active agent session." >&2; return 1
  fi
  local t="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [[ -f "${MUSE_SESSION_LOG_FILE:-}" ]]; then
    printf '{"t":"%s","event":"session_end","model_id":"%s","agent_id":"%s","start":"%s"}\n' \
      "$t" "$MUSE_SESSION_MODEL_ID" "$MUSE_SESSION_AGENT_ID" \
      "$MUSE_SESSION_START" >> "$MUSE_SESSION_LOG_FILE"
    echo "  Session log  $MUSE_SESSION_LOG_FILE"
  fi
  echo "  Session ended  ${MUSE_SESSION_MODEL_ID} / ${MUSE_SESSION_AGENT_ID}"
  unset MUSE_SESSION_MODEL_ID MUSE_SESSION_AGENT_ID \
        MUSE_SESSION_START MUSE_SESSION_LOG_FILE
  _muse_refresh 2>/dev/null
}

# muse commit wrapper that auto-injects agent identity from the active session.
# Usage: muse-agent-commit <message> [extra muse-commit flags…]
function muse-agent-commit() {
  local message="${1:?Usage: muse-agent-commit <message> [flags]}"
  shift
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  local -a extra
  [[ -n "${MUSE_SESSION_AGENT_ID}" ]] && extra+=("--agent-id" "$MUSE_SESSION_AGENT_ID")
  [[ -n "${MUSE_SESSION_MODEL_ID}" ]] && extra+=("--model-id" "$MUSE_SESSION_MODEL_ID")
  muse commit -m "$message" "${extra[@]}" "$@"
}

# List recent sessions or replay a specific session log.
# Usage: muse-sessions            → list
#        muse-sessions <file>     → replay
function muse-sessions() {
  local session_file="${1:-}"
  if [[ -z "$session_file" ]]; then
    if [[ ! -d "$MUSE_SESSION_LOG_DIR" ]]; then
      echo "No sessions yet. Start with: muse-agent-session <model_id>"
      return
    fi
    echo "  Recent agent sessions  ($MUSE_SESSION_LOG_DIR):"
    echo ""
    local f
    for f in "$MUSE_SESSION_LOG_DIR"/*.jsonl(N.Om[1,20]); do
      [[ ! -f "$f" ]] && continue
      local info
      info=$(MUSE_META_FILE="$f" python3 <<'PYEOF' 2>/dev/null
import json, os
try:
    line = open(os.environ['MUSE_META_FILE']).readline().strip()
    d = json.loads(line)
    print(f"{d.get('model_id','?'):30} {d.get('agent_id','?'):20} {d.get('t','?')[:19]}")
except Exception:
    print('?')
PYEOF
      )
      printf "  %-40s  %s\n" "${f##*/}" "$info"
    done
    return
  fi

  [[ ! -f "$session_file" ]] && { echo "File not found: $session_file" >&2; return 1; }
  MUSE_META_FILE="$session_file" python3 <<'PYEOF' 2>/dev/null
import json, os
with open(os.environ['MUSE_META_FILE']) as f:
    for line in f:
        try:
            e = json.loads(line.strip())
            t  = e.get('t', '')[:19]
            ev = e.get('event', '')
            if ev == 'session_start':
                print(f"{t}  SESSION START  model={e.get('model_id','?')}  agent={e.get('agent_id','?')}")
            elif ev == 'session_end':
                print(f"{t}  SESSION END")
            elif ev == 'cmd_end':
                print(f"{t}  EXIT {e.get('exit','?')}  ({e.get('elapsed_ms','?')}ms)")
            elif 'cmd' in e:
                print(f"{t}  $ {e['cmd']}")
        except json.JSONDecodeError:
            pass
PYEOF
}

# Internal: write command-start JSONL entry. Pure ZSH printf — no subprocess.
function _muse_session_log_start() {
  [[ -z "${MUSE_SESSION_LOG_FILE:-}" ]] && return
  local t cmd_esc cwd_esc
  t="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  cmd_esc="${1//\\/\\\\}"; cmd_esc="${cmd_esc//\"/\\\"}"
  cwd_esc="${PWD//\\/\\\\}"; cwd_esc="${cwd_esc//\"/\\\"}"
  printf '{"t":"%s","cmd":"%s","cwd":"%s","domain":"%s","branch":"%s","pid":%d}\n' \
    "$t" "$cmd_esc" "$cwd_esc" \
    "${MUSE_DOMAIN:-}" "${MUSE_BRANCH:-}" $$ \
    >> "$MUSE_SESSION_LOG_FILE" 2>/dev/null
}

# Internal: write command-end JSONL entry.
function _muse_session_log_end() {
  [[ -z "${MUSE_SESSION_LOG_FILE:-}" ]] && return
  printf '{"t":"%s","event":"cmd_end","exit":%d,"elapsed_ms":%d}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${1:-0}" "${2:-0}" \
    >> "$MUSE_SESSION_LOG_FILE" 2>/dev/null
}

# ── §9  VISUAL TOOLS ──────────────────────────────────────────────────────────

# Colorised commit graph with domain theming, SemVer badges, agent markers.
function muse-graph() {
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  cd "$MUSE_REPO_ROOT" && muse log --graph --oneline "$@" | \
  MUSE_META_DOMAIN="$MUSE_DOMAIN" python3 <<'PYEOF'
import sys, re, os

DOMAIN_COLORS = {
    'midi':     '\033[35m',
    'code':     '\033[36m',
    'bitcoin':  '\033[33m',
    'scaffold': '\033[34m',
    'genomics': '\033[32m',
}
R   = '\033[0m'
B   = '\033[1m'
DIM = '\033[2m'
YLW = '\033[33m'
RED = '\033[31m'
GRN = '\033[32m'
CYN = '\033[36m'
MAG = '\033[35m'

dc = DOMAIN_COLORS.get(os.environ.get('MUSE_META_DOMAIN', ''), '\033[37m')

for raw in sys.stdin:
    line = raw.rstrip()
    # Graph chrome
    line = line.replace('*', f'{dc}*{R}')
    line = line.replace('|', f'{DIM}|{R}')
    line = line.replace('/', f'{DIM}/{R}')
    line = line.replace('\\', f'{DIM}\\{R}')
    # Commit SHAs
    line = re.sub(r'\b([0-9a-f]{7,8})\b', f'{YLW}\\1{R}', line)
    # Branch refs
    line = re.sub(r'HEAD -> ([^\s,)]+)', f'HEAD -> {B}{GRN}\\1{R}', line)
    line = re.sub(r'\(([^)]*)\)', lambda m: f'{DIM}({m.group(1)}){R}', line)
    # SemVer badges
    line = re.sub(r'\[MAJOR\]', f'{RED}[MAJOR]{R}', line)
    line = re.sub(r'\[MINOR\]', f'{YLW}[MINOR]{R}', line)
    line = re.sub(r'\[PATCH\]', f'{GRN}[PATCH]{R}', line)
    # Agent markers
    line = re.sub(r'\[agent:([^\]]+)\]', f'{CYN}[agent:\\1]{R}', line)
    print(line)
PYEOF
}

# Vertical timeline of the last N commits (default 20) with domain theming.
function muse-timeline() {
  local n="${1:-20}"
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  local icon="${MUSE_DOMAIN_ICONS[$MUSE_DOMAIN]:-◈}"
  cd "$MUSE_REPO_ROOT" && muse log --oneline -n "$n" | \
  MUSE_META_DOMAIN="$MUSE_DOMAIN" MUSE_META_BRANCH="$MUSE_BRANCH" \
  MUSE_META_ICON="$icon" python3 <<'PYEOF'
import sys, os

DOMAIN_COLORS = {
    'midi':     '\033[35m',
    'code':     '\033[36m',
    'bitcoin':  '\033[33m',
    'scaffold': '\033[34m',
    'genomics': '\033[32m',
}
R   = '\033[0m'
YLW = '\033[33m'
DIM = '\033[2m'

domain = os.environ.get('MUSE_META_DOMAIN', '')
branch = os.environ.get('MUSE_META_BRANCH', '')
icon   = os.environ.get('MUSE_META_ICON', '◈')
dc     = DOMAIN_COLORS.get(domain, '\033[37m')

lines = [l.rstrip() for l in sys.stdin if l.strip()]
if not lines:
    print('  (no commits)')
    sys.exit(0)

print(f'  {dc}{icon} TIMELINE — {branch} (last {len(lines)} commits){R}')
print(f'  {DIM}{"─"*60}{R}')
for i, line in enumerate(lines):
    parts = line.split(None, 1)
    sha   = parts[0] if parts else '?'
    msg   = parts[1] if len(parts) > 1 else ''
    node  = f'{dc}◉{R}' if i == 0 else f'{dc}○{R}'
    print(f'  {node}  {YLW}{sha}{R}  {msg}')
    if i < len(lines) - 1:
        print(f'  {DIM}│{R}')
print(f'  {DIM}╵{R}')
PYEOF
}

# Show muse diff with bat/delta highlighting if available.
function muse-diff-preview() {
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  local output
  output=$(cd "$MUSE_REPO_ROOT" && muse diff "$@")
  if command -v delta >/dev/null 2>&1; then
    echo "$output" | delta
  elif command -v bat >/dev/null 2>&1; then
    echo "$output" | bat --style plain --language diff
  else
    echo "$output"
  fi
}

# Interactive commit browser via fzf. Enter: checkout; Ctrl-D: diff; Ctrl-Y: copy SHA.
function muse-commit-browser() {
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  if ! command -v fzf >/dev/null 2>&1; then
    echo "fzf required (https://github.com/junegunn/fzf)." >&2; return 1
  fi
  local selected
  selected=$(cd "$MUSE_REPO_ROOT" && muse log --oneline | fzf \
    --ansi \
    --no-sort \
    --preview 'muse show {1} 2>/dev/null' \
    --preview-window 'right:60%:wrap' \
    --header "↵ checkout  ctrl-d: diff  ctrl-y: copy SHA  ctrl-s: show" \
    --bind 'ctrl-d:execute(muse diff {1} 2>/dev/null | less -R)' \
    --bind 'ctrl-y:execute(echo -n {1} | pbcopy 2>/dev/null || echo -n {1} | xclip -selection clipboard 2>/dev/null; echo "copied {1}")' \
    --bind 'ctrl-s:execute(muse show {1} 2>/dev/null | less -R)' \
    --prompt "commit> ")
  if [[ -n "$selected" ]]; then
    local sha="${selected%% *}"
    echo "Checking out $sha…"
    muse checkout "$sha"
  fi
}

# Interactive branch picker via fzf. Enter: checkout; Ctrl-D: delete branch.
function muse-branch-picker() {
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  if ! command -v fzf >/dev/null 2>&1; then
    echo "fzf required (https://github.com/junegunn/fzf)." >&2; return 1
  fi
  local selected
  selected=$(cd "$MUSE_REPO_ROOT" && muse branch -v | fzf \
    --ansi \
    --preview 'echo {} | awk "{print \$1}" | sed "s/^\*//" | xargs muse log --oneline -n 8 2>/dev/null' \
    --preview-window 'right:50%' \
    --header "↵ checkout  ctrl-d: delete" \
    --bind 'ctrl-d:execute(echo {} | awk "{print \$1}" | sed "s/^\*//" | xargs muse branch -d 2>/dev/null)' \
    --prompt "branch> ")
  if [[ -n "$selected" ]]; then
    local branch
    branch=$(echo "$selected" | awk '{print $1}' | sed 's/^\*//')
    branch="${branch## }"
    [[ -n "$branch" ]] && muse checkout "$branch"
  fi
}

# Interactive stash browser via fzf. Enter: pop; Ctrl-D: drop.
function muse-stash-browser() {
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  if ! command -v fzf >/dev/null 2>&1; then
    echo "fzf required (https://github.com/junegunn/fzf)." >&2; return 1
  fi
  local selected
  selected=$(cd "$MUSE_REPO_ROOT" && muse stash list 2>/dev/null | fzf \
    --ansi \
    --preview 'echo {} | awk "{print \$1}" | xargs muse stash show 2>/dev/null' \
    --preview-window 'right:50%' \
    --header "↵ pop  ctrl-d: drop" \
    --bind 'ctrl-d:execute(echo {} | awk "{print \$1}" | xargs muse stash drop 2>/dev/null)' \
    --prompt "stash> ")
  if [[ -n "$selected" ]]; then
    local ref="${selected%% *}"
    muse stash pop "$ref"
  fi
}

# Domain-specific live state overview.
function muse-overview() {
  [[ -z "$MUSE_REPO_ROOT" ]] && { echo "Not in a muse repo." >&2; return 1; }
  local icon="${MUSE_DOMAIN_ICONS[$MUSE_DOMAIN]:-◈}"
  echo "  ${icon} ${MUSE_DOMAIN:u} OVERVIEW — ${MUSE_BRANCH}"
  case "$MUSE_DOMAIN" in
    midi)    cd "$MUSE_REPO_ROOT" && muse midi notes 2>/dev/null         ;;
    code)    cd "$MUSE_REPO_ROOT" && muse code symbols 2>/dev/null       ;;
    bitcoin) cd "$MUSE_REPO_ROOT" && muse status 2>/dev/null             ;;
    *)       muse-context                                                 ;;
  esac
}

# ── §10  POWERLEVEL10K INTEGRATION ────────────────────────────────────────────

# Left or right p10k segment. Add 'muse_vcs' to the relevant
# POWERLEVEL9K_*_PROMPT_ELEMENTS array in your .p10k.zsh.
function prompt_muse_vcs() {
  [[ -z "$MUSE_REPO_ROOT" ]] && return
  local info
  info="$(muse_prompt_info)"
  [[ -z "$info" ]] && return
  p10k segment -f white -t "$info"
}

# Shown during instant prompt (before async workers complete).
# Displays the cached state so the prompt is never blank.
function instant_prompt_muse_vcs() {
  [[ -z "$MUSE_REPO_ROOT" ]] && return
  local info
  info="$(muse_prompt_info)"
  [[ -z "$info" ]] && return
  p10k segment -f white -t "$info"
}

# ── §11  KEYBINDINGS ──────────────────────────────────────────────────────────

if [[ "$MUSE_BIND_KEYS" == "1" ]]; then
  # Ctrl+B → branch picker
  function _muse_widget_branch_picker() {
    muse-branch-picker
    zle reset-prompt
  }
  zle -N _muse_widget_branch_picker
  bindkey '^B' _muse_widget_branch_picker

  # ESC-M → commit browser (ESC then M, avoids terminal Ctrl+Shift conflicts)
  function _muse_widget_commit_browser() {
    muse-commit-browser
    zle reset-prompt
  }
  zle -N _muse_widget_commit_browser
  bindkey '\eM' _muse_widget_commit_browser

  # Ctrl+Shift+H → repo health (ESC-H)
  function _muse_widget_health() {
    echo ""
    muse-health
    zle reset-prompt
  }
  zle -N _muse_widget_health
  bindkey '\eH' _muse_widget_health
fi

# ── §12  HOOK SYSTEM ──────────────────────────────────────────────────────────

# Run user-defined post-command callbacks. Called from _muse_hook_precmd after
# a muse command completes. Users set MUSE_POST_*_CMD in their .zshrc.
function _muse_run_post_hooks() {
  local cmd="$_MUSE_LAST_CMD"
  case "$cmd" in
    muse\ commit*|mcm*|muse-agent-commit*|muse-quick-commit*|muse-wip*)
      [[ -n "$MUSE_POST_COMMIT_CMD" ]] && eval "$MUSE_POST_COMMIT_CMD" 2>/dev/null
      ;;
    muse\ checkout*|mco*)
      [[ -n "$MUSE_POST_CHECKOUT_CMD" ]] && eval "$MUSE_POST_CHECKOUT_CMD" 2>/dev/null
      ;;
    muse\ merge*|muse-safe-merge*)
      if (( ! MUSE_MERGING )); then
        [[ -n "$MUSE_POST_MERGE_CMD" ]] && eval "$MUSE_POST_MERGE_CMD" 2>/dev/null
      fi
      ;;
  esac
  _MUSE_LAST_CMD=""
}

# ── §13  COMPLETION REGISTRATION ─────────────────────────────────────────────

# Register the _muse completion function if the companion file exists alongside
# this plugin (both are installed into $ZSH_CUSTOM/plugins/muse/ by the
# install script).
if [[ -f "${0:A:h}/_muse" ]]; then
  fpath=("${0:A:h}" $fpath)
  autoload -Uz compinit
  compdef _muse muse 2>/dev/null
fi

# ── §14  INITIALISATION ───────────────────────────────────────────────────────

# Warm the cache immediately so the very first prompt shows repo state without
# an extra precmd cycle. The fast variant skips dirty detection to avoid
# blocking the shell startup.
_muse_refresh_fast 2>/dev/null
