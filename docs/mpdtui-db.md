# mpdtui DB — read / diff / write

`bin/mpdtui.py` (menu sections **5 · 6 · 7**) plus `bin/mpdtui-db-backup.sh`
(**7·1 / 7·2**).

`mpdtui` (the terminal player, `~/workspace/projects/mpdtui`) keeps per-track
play counts, ratings, marks and tags in `~/.config/mpdtui/mpdtui.db`. Nothing
in the music-metadata workflow touched it before; this section reads it,
reconciles it against the library and Apple Music, and edits it.

## Schema it expects (as of 2026-09)

```
tracks       id, normalized_path, real_path, play_count, rating (0-5), updated_at
mark_reason  id, reason                     (catalog; 7 seeded rows)
tags         id, tagname                    (catalog; bengali/hindi/english seeded)
track_marks  track_id, mark_id              many-to-many
track_tags   track_id, tag_id               many-to-many
```

`real_path` is repo-relative and lives on disk at `$MUSIC_DIR/<real_path>`.
`normalized_path` is each path segment folded to just its Unicode
letters/digits, lowercased — `mpdtui.py`'s `normalize()` mirrors mpdtui's
`internal/metadata.normalizeSegment`.

> **mpdtui is under active development and its schema moves.** Marks were a
> single `tracks.mark` column until recently; they are now a join table. If
> `mpdtui.py` prints `no such column / table`, open mpdtui once (it migrates on
> open) or update the script. The `connect()` guard names the missing table.

## Read (section 5)

```
mpdtui.py summary                     counts: rated / played / marked, per-mark, per-tag
mpdtui.py list [--min-rating N] [--mark ID] [--tag NAME] [--played]
mpdtui.py marks                       the mark_reason vocabulary (id  reason)
mpdtui.py tags                        tags + track counts
mpdtui.py orphans                     rows whose file is gone
```

`list` prints `id ⇥ rating ⇥ play_count ⇥ marks ⇥ real_path` — pipe it through
`fzf -m` into a write command.

## Diff (section 6)

```
mpdtui.py diff-library     orphan rows + path drift; "no DB row yet" as a count
                           (--full to list — it's ~6.7k un-rated tracks, not a problem)
mpdtui.py diff-stats       rating (mpdtui vs Apple user-rating /20) and play_count
                           divergence, joined via play_stats.csv join_key
```

## Write (section 7)

Every write is a **dry run** unless `--apply`, prompts unless `--yes`, and
copies the DB to `<db>.bak-<timestamp>` before the first change.

```
<ids> | mpdtui.py mark --add "Syncing needed" --apply
<ids> | mpdtui.py mark --remove 2 --apply
<ids> | mpdtui.py mark --clear --apply            # drop all marks on those tracks
<ids> | mpdtui.py tag  --add bengali --apply
        mpdtui.py tag  --rename english=en --apply
        mpdtui.py import-ratings --apply           # pull Apple user-ratings in
        mpdtui.py prune --apply                    # delete orphan rows
```

`<ids>` is any text with a leading integer id per line — i.e. `mpdtui.py list`
piped through `fzf -m`.

### Full remote backup / restore

`bin/mpdtui-db-backup.sh backup | restore | list` — a verbatim copy of the
dotfiles script. Snapshots `$MPDTUI_DB` (consistent `.backup` + a `.sql` dump)
to an rclone remote at `<remote>:Backup/mpdtui/`, keeps the newest 30 per host,
and can restore any snapshot. Prompts for the remote (default `gdrive`).
