#!/usr/bin/env python3
"""refresh-reports.py - regenerate the still_missing_* coverage lists from what
is on disk right now under the lyrics collection.

Never trusts a previous report, only the filesystem. Reads meta_genre.json
(frozen per-track metadata) from the reports dir, checks each track for a
.txt/.lrc at the mirrored path, and rewrites:

  <reports>/still_missing_songs.{jsonl,txt}
  <reports>/still_missing_nonlyrical.{jsonl,txt}
  <reports>/README.md         (live counts + a description of every file there)

    refresh-reports.py                      # uses $MUSIC_METADATA_DIR
    refresh-reports.py --lyrics DIR --reports DIR

Was music-metadata/lyrics-reports/refresh.py. Stdlib only; read-only against
the audio collection.
"""
import argparse
import datetime
import json
import os
from pathlib import Path


def has_lyrics(lyrics_dir, rel):
    base = lyrics_dir / os.path.splitext(rel)[0]
    return base.with_suffix(".txt").exists() or base.with_suffix(".lrc").exists()


def dump(out_dir, name, rows, cols):
    (out_dir / f"{name}.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (out_dir / f"{name}.txt").write_text(
        "".join("\t".join(str(r[c]).replace("\n", "\\n") for c in cols) + "\n" for r in rows),
        encoding="utf-8")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mm = os.environ.get("MUSIC_METADATA_DIR")
    ap.add_argument("--lyrics", default=(f"{mm}/lyrics" if mm else None),
                    help="lyrics collection root (default: $MUSIC_METADATA_DIR/lyrics)")
    ap.add_argument("--reports", default=(f"{mm}/lyrics-reports" if mm else None),
                    help="reports dir holding meta_genre.json (default: $MUSIC_METADATA_DIR/lyrics-reports)")
    args = ap.parse_args()
    if not args.lyrics or not args.reports:
        ap.error("pass --lyrics/--reports or set $MUSIC_METADATA_DIR")

    lyrics_dir = Path(args.lyrics).expanduser().resolve()
    reports = Path(args.reports).expanduser().resolve()
    meta_path = reports / "meta_genre.json"
    if not meta_path.is_file():
        ap.error(f"meta_genre.json not found in {reports}")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    missing = [m for m in meta if not has_lyrics(lyrics_dir, m["rel"])]
    songs = [m for m in missing if not m["nonlyrical"]]
    nonlyr = [m for m in missing if m["nonlyrical"]]
    covered = len(meta) - len(missing)

    def row(m):
        return {"rel": m["rel"], "title": m["tag_title"] or m["file_title"],
                "artist": m["tag_artist"] or m["dir_artist"],
                "genre": m.get("genre", ""), "duration": round(m["duration"] or 0)}

    cols = ["rel", "title", "artist", "genre", "duration"]
    n1 = dump(reports, "still_missing_songs", sorted((row(m) for m in songs), key=lambda x: x["rel"]), cols)
    n2 = dump(reports, "still_missing_nonlyrical", sorted((row(m) for m in nonlyr), key=lambda x: x["rel"]), cols)

    (reports / "README.md").write_text(f"""# lyrics-reports

Last refreshed {datetime.date.today().isoformat()} by `music-tui` (menu 1·2 /
`bin/refresh-reports.py`), from the live state of `{lyrics_dir}`.

Of the {len(meta)} tracks originally listed in missing_lrc.txt:
  {covered:5d}  now have lyrics
  {n1:5d}  real songs still missing         -> still_missing_songs.{{jsonl,txt}}
  {n2:5d}  instrumental / score / spoken word, no lyrics exist -> still_missing_nonlyrical.{{jsonl,txt}}

## Files

still_missing_songs.{{jsonl,txt}}
    Real songs with no lyrics yet. Columns: rel, title, artist, genre, duration.

still_missing_nonlyrical.{{jsonl,txt}}
    Unmatched tracks judged instrumental / score / spoken word. A judgement
    call - if one here does have lyrics it self-corrects once a file exists.

meta_genre.json
    Frozen per-track metadata (ffprobe tags, duration, genre, nonlyrical flag)
    captured 2026-08-18. The refresh reads this for "all tracks"; it does not
    re-scan the audio.

align_results.jsonl / align_review.txt
    Output of the bansuri forced-alignment pass (.txt -> .lrc). accept | reject
    | error per track. See $TOOLS_PATH/bansuri.

results.jsonl
    Raw per-track outcome of the original LRCLIB fetch pass.

review_plain_matches.txt
    LRCLIB matches from a different-length recording - words right, timings
    not, so .txt kept and .lrc withheld.

rs_scored.{{json,txt}} / rs_review.txt / gitabitan_index.json
    Rabindra Sangeet matching against Bengali Wikisource's Gitabitan.

## Caveat

A directory under soumi/ has literal newlines in its name; the .txt lists
escape them as \\n, the .jsonl round-trips.
""", encoding="utf-8")

    print(f"tracks tracked      {len(meta)}")
    print(f"  now have lyrics   {covered}")
    print(f"  songs missing     {n1}")
    print(f"  non-lyrical       {n2}")


if __name__ == "__main__":
    main()
