# Pushing lyrics out

Two destinations, both reached from menu **section 3** and both driven by
`bin/lyrics-push.py`. A lyrics file at `$MUSIC_METADATA_DIR/lyrics/<rel>` goes
to the **same nested `<rel>`** under the destination — the music collection
mirrors the lyrics tree exactly.

## What "uncommitted" means

`lyrics-push.py list` runs `git status --porcelain` in `$MUSIC_METADATA_DIR`
and keeps every `.txt` / `.lrc` / `.elrc` under `lyrics/` that is **untracked
or modified and not yet committed** — i.e. what you just fetched or hand-edited.
It excludes lrc-sync's `*.old` backups and deleted paths. The music-metadata
working tree is never touched: everything here is a **copy**.

## 3·2 — copy next to the mp3 (local, for sync testing)

```
lyrics-push.py list | fzf -m | lyrics-push.py to-music
```

Copies each picked file to `$MUSIC_DIR/<rel>`, so it sits beside the track and
`mpd` / `mpdtui` pick it up on the next play — the point is to *hear* whether a
freshly-made `.lrc` lines up before you commit it. A sidecar that already
exists there is overwritten, after one `[y/N]` confirm.

## 3·3 / 3·4 — push to Google Drive (rclone)

```
lyrics-push.py list | fzf -m | lyrics-push.py to-drive [--dry-run]
```

`rclone copyto "$MUSIC_METADATA_DIR/lyrics/<rel>" "$RCLONE_MUSIC_REMOTE_PATH/<rel>"`
per file. Assumes rclone is configured and `RCLONE_MUSIC_REMOTE_PATH` in the
config points at the music collection's base on the remote (default
`gdrive:Media/Music`). `--dry-run` prints the commands and copies nothing.

## Bulk sync (not wired into the menu)

To push **every** committed lyrics file to Drive in one shot, mirroring the
tree and skipping what's already there:

```bash
rclone copy "$MUSIC_METADATA_DIR/lyrics" "$RCLONE_MUSIC_REMOTE_PATH" \
    --include '*.lrc' --include '*.txt' --include '*.elrc' \
    --ignore-existing --progress
```

Drop `--ignore-existing` to also refresh files whose content changed.

---

## Appendix — the old macOS method (`HOW_TO_COPY.md`)

Before rclone this was done on the Mac with Python one-liners copying
`lyrics/*.{lrc,txt}` into
`~/Library/CloudStorage/GoogleDrive-…/My Drive/Media/Music`, skipping files
that already existed. Kept only for reference; the rclone flow above replaces
it.
