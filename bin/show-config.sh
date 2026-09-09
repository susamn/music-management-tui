#!/usr/bin/env bash
# Print the resolved music-tui config and check each path exists.
set -u
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/music-tui/config"

echo "config file: $CONF"
echo

check() {  # name value  [expect: dir|file|remote|any]
  local name="$1" val="$2" kind="${3:-any}" mark
  case "$kind" in
    dir)    [[ -d "$val" ]] && mark="ok   " || mark="MISS " ;;
    file)   [[ -f "$val" ]] && mark="ok   " || mark="MISS " ;;
    remote) if rclone listremotes 2>/dev/null | grep -q "^${val%%:*}:"; then mark="ok   "; else mark="MISS "; fi ;;
    *)      mark="     " ;;
  esac
  printf '  %s %-26s %s\n' "$mark" "$name" "$val"
}

check MUSIC_METADATA_DIR      "${MUSIC_METADATA_DIR:-}"      dir
check MUSIC_DIR               "${MUSIC_DIR:-}"               dir
check MPDTUI_DB               "${MPDTUI_DB:-}"               file
check PLAY_STATS_CSV          "${PLAY_STATS_CSV:-}"          file
check FILES_TREE              "${FILES_TREE:-}"              file
check RCLONE_MUSIC_REMOTE_PATH "${RCLONE_MUSIC_REMOTE_PATH:-}" remote
printf '  %s %-26s %s\n' "     " THEME "${THEME:-obsidian}"

echo
echo "lyrics dir:   ${MUSIC_METADATA_DIR:-?}/lyrics    ($( [[ -d "${MUSIC_METADATA_DIR:-/nonexistent}/lyrics" ]] && echo present || echo MISSING ))"
echo "playlists:    ${MUSIC_METADATA_DIR:-?}/playlists ($( [[ -d "${MUSIC_METADATA_DIR:-/nonexistent}/playlists" ]] && echo present || echo MISSING ))"
