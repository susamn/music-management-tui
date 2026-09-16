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

# --- existing config: adopt keys the template has gained since ----------------
# Without this, a config written before a feature existed never learns about it,
# and the only symptom is a script quietly falling back to its defaults. Only
# ever appends; an existing value is never touched.
python3 - "$SELF/config.example" "$CONF" <<'MERGE_PY'
import re, sys
example, conf = sys.argv[1], sys.argv[2]
KEY = re.compile(r"^([A-Z][A-Z0-9_]*)=")

have = set()
for line in open(conf, encoding="utf-8"):
    m = KEY.match(line)
    if m:
        have.add(m.group(1))

buf, add = [], []
for line in open(example, encoding="utf-8"):
    m = KEY.match(line)
    if not m:
        buf.append(line)
        continue
    if m.group(1) not in have:
        add.extend(buf + [line])
    buf = []

if add:
    with open(conf, "a", encoding="utf-8") as fh:
        fh.write("\n" + "".join(add).strip("\n") + "\n")
    keys = [KEY.match(l).group(1) for l in add if KEY.match(l)]
    print("music-tui: added config keys to %s: %s" % (conf, ", ".join(keys)), file=sys.stderr)
MERGE_PY
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
: "${WIKI_DIR:=$MUSIC_METADATA_DIR/wiki}"
: "${WIKI_REPORTS_DIR:=$MUSIC_METADATA_DIR/wiki-reports}"
: "${WIKI_BATCH:=100}"

# expand a leading ~ that survived (e.g. a hand-edited value in quotes)
for v in MUSIC_METADATA_DIR MUSIC_DIR MPDTUI_DB PLAY_STATS_CSV FILES_TREE WIKI_DIR WIKI_REPORTS_DIR; do
  printf -v "$v" '%s' "${!v/#\~/$HOME}"
done

export SELF MUSIC_METADATA_DIR MUSIC_DIR MPDTUI_DB PLAY_STATS_CSV FILES_TREE RCLONE_MUSIC_REMOTE_PATH
export WIKI_DIR WIKI_REPORTS_DIR WIKI_BATCH WIKI_LANG WIKI_USER_AGENT
export MUSICBRAINZ_API COVERART_API WIKIPEDIA_API LASTFM_API GENIUS_API DISCOGS_API
export LASTFM_API_KEY GENIUS_TOKEN DISCOGS_TOKEN

# --- generate the menu (only @THEME@ needs substituting; $VARS expand at run
#     time via the engine's shell=True) ---------------------------------------
sed "s/@THEME@/${THEME//\//\\/}/" "$SELF/menu.tmpl.json" > "$GEN_MENU"

exec python3 "$SELF/engine.py" "$GEN_MENU"
