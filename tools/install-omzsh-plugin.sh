#!/usr/bin/env bash
# install-omzsh-plugin.sh — Install the Muse Oh My ZSH plugin
# ─────────────────────────────────────────────────────────────
# Run from the muse repo root:
#   bash tools/install-omzsh-plugin.sh
#
# What it does:
#   1. Locates the Oh My ZSH custom plugin directory
#      (respects $ZSH_CUSTOM if set, otherwise ~/.oh-my-zsh/custom)
#   2. Creates the muse/ subdirectory inside plugins/
#   3. Symlinks muse.plugin.zsh and _muse from tools/omzsh-plugin/
#      so updates to the Muse repo automatically update the plugin
#   4. Prints the one line you need to add to ~/.zshrc
#
# To uninstall:
#   rm -rf "${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/muse"

set -euo pipefail

# ── Resolve paths ─────────────────────────────────────────────────────────────

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN_SRC="$REPO_DIR/tools/omzsh-plugin"

# Respect $ZSH_CUSTOM if the user has set it (e.g. for Zinit, Antigen, etc.)
ZSH_CUSTOM_DIR="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}"
PLUGIN_DST="$ZSH_CUSTOM_DIR/plugins/muse"

# ── Sanity checks ─────────────────────────────────────────────────────────────

if [[ ! -f "$PLUGIN_SRC/muse.plugin.zsh" ]]; then
  echo "ERROR: Plugin source not found at $PLUGIN_SRC/muse.plugin.zsh" >&2
  echo "       Run this script from the muse repository root." >&2
  exit 1
fi

if [[ ! -d "$ZSH_CUSTOM_DIR" ]]; then
  echo "ERROR: Oh My ZSH custom directory not found at $ZSH_CUSTOM_DIR" >&2
  echo "       Install Oh My ZSH first: https://ohmyz.sh" >&2
  echo "       Or set \$ZSH_CUSTOM to your custom plugin directory." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "WARNING: python3 not found in PATH." >&2
  echo "         The plugin requires python3 for JSON/TOML parsing." >&2
  echo "         Install it before using the plugin." >&2
fi

# ── Create plugin directory and symlinks ──────────────────────────────────────

mkdir -p "$PLUGIN_DST"

# Use -f to replace existing symlinks (handles re-runs and updates cleanly).
ln -sf "$PLUGIN_SRC/muse.plugin.zsh" "$PLUGIN_DST/muse.plugin.zsh"
ln -sf "$PLUGIN_SRC/_muse"           "$PLUGIN_DST/_muse"

# ── Report ────────────────────────────────────────────────────────────────────

echo ""
echo "  Muse Oh My ZSH plugin installed"
echo ""
echo "  plugin dir  $PLUGIN_DST"
echo "  source      $PLUGIN_SRC  (symlinked — updates automatically)"
echo ""
echo "  Next step: add 'muse' to the plugins array in your ~/.zshrc:"
echo ""
echo "    plugins=(git muse)"
echo ""
echo "  Then reload your shell:"
echo ""
echo "    source ~/.zshrc"
echo ""

# ── Powerlevel10k hint ────────────────────────────────────────────────────────

if [[ -f "$HOME/.p10k.zsh" ]]; then
  echo "  Powerlevel10k detected."
  echo "  Add 'muse_vcs' to POWERLEVEL9K_LEFT_PROMPT_ELEMENTS or"
  echo "  POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS in ~/.p10k.zsh:"
  echo ""
  echo "    POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS=(... muse_vcs ...)"
  echo ""
fi

# ── fzf hint ──────────────────────────────────────────────────────────────────

if ! command -v fzf >/dev/null 2>&1; then
  echo "  Optional: install fzf to unlock interactive tools"
  echo "  (branch picker, commit browser, stash browser):"
  echo ""
  echo "    brew install fzf   # macOS"
  echo "    apt install fzf    # Debian/Ubuntu"
  echo ""
fi

echo "  Full docs: docs/reference/omzsh-plugin.md"
echo ""
