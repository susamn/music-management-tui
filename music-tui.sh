#!/usr/bin/env bash
#
# music-tui -- one indexed menu over the music-metadata workflow.
# Loads ~/.config/music-tui/config, exports the paths, generates the menu
# from menu.tmpl.json, and hands off to engine.py.
#
set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/music-tui"
CONF="$CONF_DIR/config"
GEN_MENU="$CONF_DIR/menu.json"

mkdir -p "$CONF_DIR"

# --- first run: seed the config from the template -----------------------------
if [[ ! -f "$CONF" ]]; then
  cp "$SELF/config.example" "$CONF"
  # Prefer mpdtui's own music_dir if it is configured, so the two agree.
  mpdtui_conf="${XDG_CONFIG_HOME:-$HOME/.config}/mpdtui/config"
  if [[ -f "$mpdtui_conf" ]]; then
    md="$(sed -n 's/^[[:space:]]*music_dir[[:space:]]*=[[:space:]]*//p' "$mpdtui_conf" | head -1)"
    md="${md%/}"
    [[ -n "$md" ]] && sed -i "s|^MUSIC_DIR=.*|MUSIC_DIR=$md|" "$CONF"
  fi
  echo "music-tui: wrote a new config at $CONF -- review it (Settings section, or \$EDITOR)." >&2
fi

# --- load config -------------------------------------------------------------
# shellcheck disable=SC1090
source "$CONF"

: "${MUSIC_METADATA_DIR:?set MUSIC_METADATA_DIR in $CONF}"
: "${MUSIC_DIR:?set MUSIC_DIR in $CONF}"
: "${MPDTUI_DB:=$HOME/.config/mpdtui/mpdtui.db}"
: "${PLAY_STATS_CSV:=$MUSIC_METADATA_DIR/play-stats/play_stats.csv}"
: "${FILES_TREE:=$MUSIC_METADATA_DIR/files.tree}"
: "${RCLONE_MUSIC_REMOTE_PATH:=gdrive:Media/Music}"
: "${THEME:=obsidian}"

# expand a leading ~ that survived (e.g. a hand-edited value in quotes)
for v in MUSIC_METADATA_DIR MUSIC_DIR MPDTUI_DB PLAY_STATS_CSV FILES_TREE; do
  printf -v "$v" '%s' "${!v/#\~/$HOME}"
done

export SELF MUSIC_METADATA_DIR MUSIC_DIR MPDTUI_DB PLAY_STATS_CSV FILES_TREE RCLONE_MUSIC_REMOTE_PATH

# --- generate the menu (only @THEME@ needs substituting; $VARS expand at run
#     time via the engine's shell=True) ---------------------------------------
sed "s/@THEME@/${THEME//\//\\/}/" "$SELF/menu.tmpl.json" > "$GEN_MENU"

exec python3 "$SELF/engine.py" "$GEN_MENU"
