# lyrics-reports (refresh)

`bin/refresh-reports.py` — menu **1·2**. Regenerates the "what lyrics are still
missing" lists from what is on disk **right now**, never trusting a previous
report.

## What it does

Reads `meta_genre.json` (frozen per-track metadata, captured 2026-08-18) from
the reports dir, checks each of those ~3675 tracks for a `.txt` or `.lrc` at
the mirrored path under the lyrics collection, and rewrites, in the reports
dir:

```
still_missing_songs.{jsonl,txt}        real songs with no lyrics yet
still_missing_nonlyrical.{jsonl,txt}   instrumental / score / spoken word
README.md                             live counts + a description of every file there
```

`.jsonl` is lossless; `.txt` is for reading (one dir under `soumi/` has literal
newlines in its name, escaped as `\n` in the `.txt`).

## Run

```bash
music-tui.sh                                   # → section 1 → item 2
bin/refresh-reports.py                          # uses $MUSIC_METADATA_DIR
bin/refresh-reports.py --lyrics DIR --reports DIR
```

Read-only against the audio collection; only writes into the reports dir. The
reports dir and `meta_genre.json` live in **`music-metadata/lyrics-reports/`**
(data stays with the data repo).

## Related, not run from here

`align_results.jsonl` / `align_review.txt` in the reports dir come from the
`bansuri` forced-alignment tool (`$TOOLS_PATH/bansuri`), and `rs_scored.*` /
`gitabitan_index.json` from the Rabindra Sangeet ↔ Gitabitan matching pass —
both separate from this refresh.
