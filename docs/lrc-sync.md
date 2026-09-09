# lrc-sync

A small terminal tool to hand-time plain `.txt` lyrics into synced `.lrc`
files, driven by whatever your MPD server is currently playing.

It does **not** fetch or guess anything. You play the song, you tap a key on
every line, it records a self-run clock. That's the whole idea.

`bin/lrc-sync.py` — single file, Python 3 stdlib only (curses + a tiny MPD
client). Menu item **1·1**, or run it directly.

## Requirements

- Python 3 (standard library only — no `pip install`).
- A running MPD server whose `music_directory` mirrors
  `$MUSIC_METADATA_DIR/lyrics/` — MPD plays `adele/21/12-hello….mp3`, the
  lyrics live at `$MUSIC_METADATA_DIR/lyrics/adele/21/12-hello….txt`.
- Optional: `MPD_HOST` / `MPD_PORT` env vars (same convention as `mpc`).
  Defaults to `localhost:6600`. `MPD_HOST` may be `password@host`.
- `$EDITOR` (falls back to `vi`) — used only for pasting / editing a `.txt`.

## Run

```bash
music-tui.sh                                   # → section 1 → item 1
MUSIC_METADATA_DIR=~/Music/music-metadata bin/lrc-sync.py
bin/lrc-sync.py --lyrics ~/Music/music-metadata/lyrics
```

The lyrics dir is `--lyrics`, else `$MUSIC_METADATA_DIR/lyrics`, else
`../../lyrics` relative to the script.

## How paths map

The tool asks MPD for the current song and gets a path **relative to MPD's
music directory**, e.g.

```
a/b/c/track.mp3
```

It then works with:

```
lyrics/a/b/c/track.txt      plain lyrics (input)
lyrics/a/b/c/track.lrc      synced lyrics (output)
```

Same relative folders, same basename, only the extension changes. Folders are
created as needed.

## The flow

### Main screen

Follows MPD live and shows one of three states for the current track:

| State on disk        | Options offered                                          |
|----------------------|--------------------------------------------------------|
| no `.txt`            | `[p]` paste `.txt` lyrics — **cannot sync yet**         |
| `.txt`, no `.lrc`    | `[c]` convert `.txt` → `.lrc` (manual sync) · `[e]` edit `.txt` |
| `.txt` **and** `.lrc`| `[o]` re-sync `.lrc` · `[e]` edit `.txt`                 |

`✓ .txt found` is shown in green, `✓ .lrc found` in orange (missing → red).

Keys: `[p]` / `[c]` / `[o]` / `[e]` as above, `[r]` refresh, `[q]` quit.

### Editing an existing `.txt` (`[e]`)

Opens `$EDITOR` on the current `.txt` (line splits, typos, extra blank lines
for instrumental gaps). Saving writes it straight back to the same path. An
empty buffer is ignored. Re-run `[c]`/`[o]` afterwards to re-time.

### Pasting a missing `.txt`

`[p]` opens `$EDITOR` on an empty temp file. Paste the lyrics, save, quit the
editor. The tool writes `lyrics/<mirrored path>.txt` and returns to the main
screen, which now offers `[c]`. An empty editor saves nothing.

(To fix an existing `.txt`, use `[e]` on the main screen — see below.)

### Syncing (`[c]` or `[o]`)

The track is **pinned** the moment you press the key:

- The sync view never switches to another `.txt`, whatever MPD does.
- Entering the sync view also flips MPD to `single` on (with `repeat` and
  `consume` off) so playback **stops at the end of this song instead of
  rolling on to the next one**. Your previous `single` / `repeat` / `consume`
  settings are restored when you leave the sync view.
- As a second line of defence, the tool also watches MPD's current song id
  while you're in the sync view. If it ever changes to anything other than
  the pinned track — end of song, `single` not honoured, someone hits "next"
  on another client — the tool immediately issues `stop`. The track you're
  timing will not silently keep rolling.

**Timing is measured by an internal clock, not by MPD's reported `elapsed`.**
`elapsed` can sit seconds off from the sound actually reaching your ears
(output buffers, resampling, bluetooth, network sinks), which pushes every
`.lrc` line off by that much. A "press this the instant you hear it" key
doesn't fix that either — it just adds your own reaction time on top. So the
tool drives both ends itself:

1. Press **`space`**. This makes the tool seek the pinned track to 0:00, start
   it playing, zero the internal clock, and stamp the landing line at 0:00 —
   all one action, no ear-timing involved.
2. Press **`n`** on each line as it's sung. Each press marks the moment you
   **enter** that line — the cursor moves onto it and *then* it's stamped —
   not the moment you leave the one before it. So the first `n` after
   `space` enters and stamps line 1 (the first real lyric), the next enters
   line 2, and so on.

Every `n` stamp also has a fixed `STAMP_OFFSET` (0.5s, see the top of
`lrc-sync.py`) subtracted from it, floored at 0:00 — a `n` press always lands
a little after the line's true onset (your reaction time), so this pulls it
back. The landing line stays exactly `00:00.00`, unaffected. Adjust the
constant if half a second over- or under-shoots your own reaction time.

If MPD pauses/stops mid-pass the clock freezes and resumes with it, so a short
break doesn't wreck the take. (Pressing `n` before ever pressing `space` just
zeroes the clock and stamps the landing line at that moment, without touching
MPD — for continuing a take that's already mid-playback. That press alone
doesn't advance past the landing line; press `n` again to enter line 1.
Normally you want `space` first.)

Sync view:

- The `.txt` lines are listed read-only. Blank lines (stanza breaks) are kept
  as real lines — you step through them too, matching the existing `.lrc`
  files in the lyrics collection which carry timestamped blank lines for
  instrumental gaps.
- A blank **landing line** is always inserted before line 1, unless the `.txt`
  already opens with one. It represents 0:00 itself — the tool stamps it
  automatically whenever the clock is (re)started, covering a song's intro
  before the singing begins so that gap doesn't land on the first real line.
  It's display/output only — nothing is written into the `.txt` file itself.
- Header shows the internal `clock`, MPD's own `state`/`elapsed` (reference
  only), and `line X / N`.
- Lines already stamped this session are shown in green.

| Key       | Action                                                          |
|-----------|----------------------------------------------------------------|
| `space`   | Restart the track at 0:00 (real MPD seek+play), zero the clock, and stamp the landing line — together. |
| `n`       | Move onto the next line and stamp it at the current clock position — i.e. the moment you *enter* it. |
| `↑` / `↓` (or `k` / `j`) | Move the cursor **without** stamping — use this to park on the line where you want to resume, then press `n`. |
| `b`       | Undo: clear the current line's timestamp and step back to it (fix a fumble, then re-enter it with `n`). |
| `s`       | **Save now.** Writes an `.lrc` with the lines stamped so far.    |
| `Esc`     | Leave the sync view. Nothing is written, but the timing so far is kept (see Resume). |

### Resume

Every stamp is kept in memory for the whole session, per track. If you leave
the sync view (`Esc`, or `s` to save a partial pass) and come back to the same
track via `[c]` / `[o]`, it reopens with everything you'd already timed and the
cursor sitting exactly on the last line you entered — the main screen shows
`[resume available]`. To continue: press `space` (restarts the track at 0:00
and zeroes the clock) and carry straight on with `n` — the clock is always
relative to the song's start, so old and new stamps line up. Use `↑`/`↓` first
only if you want to jump to a different line than where you left off. History
is only lost when you quit the tool.

### Editing the `.txt` mid-sync

Leave the sync view, hit `[e]` on the main screen, fix the lyrics, save. Re-enter
the sync view: the new text is used, and every already-timed line whose text
still matches **keeps its timestamp** — only the lines you actually changed
(and anything after an inserted/removed line) lose theirs and need re-timing.
The saved `.lrc` always uses the current `.txt` text.

### Output and the old `.lrc`

On save:

- If `lyrics/…/track.lrc` already exists it is renamed to
  `track.lrc.old` first (and `track.lrc.old.<epoch>` if `.old` is already
  taken). Nothing is ever overwritten in place.
- The new file is written as `lyrics/…/track.lrc` with lines like:

  ```
  [00:08.42] first line
  [00:11.90] second line
  [00:14.05]
  ```

  `mm:ss.cc` (centiseconds), a space after the bracket, blank text for blank
  lines — the format already used across the lyrics collection.

## Notes / limits

- Timestamp accuracy is your reaction time plus one MPD `status` round-trip
  (sub-millisecond on a local socket). Use `b` to redo a line, or re-run `[o]`
  for another pass.
- Radio streams / non-file sources are shown but not timeable.
- The tool never touches audio files and never deletes anything — the worst it
  does is rename an old `.lrc` to `.lrc.old`.
- After adding files, run the coverage-report refresh (menu **1·2**) to update
  the still-missing lists.
