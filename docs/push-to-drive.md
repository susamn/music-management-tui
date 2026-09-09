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

## 3·5 / 3·6 / 3·7 — push a past commit's lyrics to Drive

```
lyrics-push.py commits [-n 5]                         list, with pushed status
lyrics-push.py commits | fzf -m | lyrics-push.py push-commits [--dry-run]
```

`commits` shows the last N commits that touched `lyrics/`, each tagged
`[new]` or `[pushed <date>]`, with how many lyrics files it changed.
`push-commits` takes the picked commits, collects every `.txt`/`.lrc`/`.elrc`
they added or modified (the current working-tree version of each — a file
deleted since is skipped), `rclone copyto`s them to Drive, and on success
appends one line per commit to the **push ledger**.

### The push ledger — `music-metadata/lyrics-reports/drive-push.log`

Lives **in the music-metadata repo** so it travels with the data and is the
same on every machine. One line per pushed commit:

```
<full-hash>	<iso timestamp>	<n files>	<commit subject>
```

`push-commits` only **appends** to it — it never commits. After a real push
the file is dirty in music-metadata; commit it yourself (alongside the next
lyrics commit, or on its own). A commit re-pushed just gets another line;
`commits` shows the most recent push date.

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
