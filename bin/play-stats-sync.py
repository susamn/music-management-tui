#!/usr/bin/env python3
"""sync.py - refresh play-stats/play_stats.csv from Apple Music.app.

Runs extract.js (JXA) to snapshot every track's rating / play count / etc.,
then MERGES that into play_stats.csv:

  * rows are keyed on persistent_id (stable across renames in Music.app)
  * the columns extract.js owns are overwritten with the fresh values
  * any extra columns you added to play_stats.csv by hand are preserved
  * rows in play_stats.csv that Music.app no longer reports are kept, with
    stale=1 set (so nothing you annotated silently vanishes)

Safe to run again and again. Read-only against Music.app.

    python3 play-stats/sync.py            # merge in place
    python3 play-stats/sync.py --dry-run  # report what would change, write nothing
    python3 play-stats/sync.py --out X    # write to X instead of play_stats.csv

Requires macOS with Music.app. Stdlib only.

CSV path: $PLAY_STATS_CSV, else $MUSIC_METADATA_DIR/play-stats/play_stats.csv
(override with --out). Was music-metadata/play-stats/sync.py.
"""
import argparse
import csv
import io
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXTRACT = HERE / "extract.js"
if os.environ.get("PLAY_STATS_CSV"):
    CSV_PATH = Path(os.environ["PLAY_STATS_CSV"]).expanduser()
elif os.environ.get("MUSIC_METADATA_DIR"):
    CSV_PATH = Path(os.environ["MUSIC_METADATA_DIR"]).expanduser() / "play-stats" / "play_stats.csv"
else:
    CSV_PATH = HERE / "play_stats.csv"

# Columns produced (and thus owned) by extract.js. persistent_id is the key.
MANAGED = ["persistent_id", "path_abs", "path_lib", "path_slug", "join_key",
           "artist", "album", "title", "track_number", "disc_number", "rating",
           "rating_kind", "play_count", "skip_count", "played_date",
           "cloud_status"]
KEY = "persistent_id"
# Compared for the change summary only.
WATCH = ["rating", "rating_kind", "play_count", "skip_count", "played_date"]


def snapshot():
    if not EXTRACT.exists():
        sys.exit(f"missing {EXTRACT}")
    try:
        proc = subprocess.run(
            ["osascript", "-l", "JavaScript", str(EXTRACT)],
            capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit("osascript not found - this needs macOS.")
    except subprocess.CalledProcessError as e:
        sys.exit(f"extract.js failed:\n{e.stderr.strip()}")
    rows = list(csv.DictReader(io.StringIO(proc.stdout)))
    if not rows:
        sys.exit("extract.js returned no rows - is Music.app available?")
    return rows


def load_existing(path):
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader), list(reader.fieldnames or [])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report changes, write nothing")
    ap.add_argument("--out", type=Path, default=CSV_PATH,
                    help=f"output path (default {CSV_PATH.name})")
    args = ap.parse_args()

    fresh = snapshot()
    existing_rows, existing_cols = load_existing(args.out)
    extra_cols = [c for c in existing_cols if c not in MANAGED and c != "stale"]
    out_cols = MANAGED + extra_cols + ["stale"]

    by_key = {r[KEY]: r for r in existing_rows if r.get(KEY)}

    added, updated, unchanged = 0, 0, 0
    changes = []  # (title, field, old, new)
    seen = set()

    for row in fresh:
        k = row[KEY]
        seen.add(k)
        if k not in by_key:
            new = {c: "" for c in out_cols}
            new.update(row)
            new["stale"] = "0"
            by_key[k] = new
            added += 1
            continue
        cur = by_key[k]
        row_changed = False
        for f in MANAGED:
            old_v = cur.get(f, "")
            new_v = row.get(f, "")
            if old_v != new_v:
                if f in WATCH:
                    changes.append((row.get("title", ""), f, old_v, new_v))
                cur[f] = new_v
                row_changed = True
        if cur.get("stale") not in ("", "0"):
            row_changed = True
        cur["stale"] = "0"
        updated += row_changed
        unchanged += not row_changed

    stale = 0
    for k, row in by_key.items():
        if k not in seen:
            stale += 1
            row["stale"] = "1"

    ordered = sorted(by_key.values(),
                     key=lambda r: (r.get("path_lib", ""), r.get(KEY, "")))

    print(f"snapshot: {len(fresh)} tracks from Music.app")
    print(f"  new       {added}")
    print(f"  updated   {updated}")
    print(f"  unchanged {unchanged}")
    print(f"  stale (in csv, not in Music) {stale}")
    if changes:
        print(f"\n  {len(changes)} rating/play changes:")
        for title, f, old, new in changes[:40]:
            print(f"    {title[:45]:45s}  {f}: {old or '-'} -> {new or '-'}")
        if len(changes) > 40:
            print(f"    ... and {len(changes) - 40} more")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return

    tmp = args.out.with_suffix(args.out.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=out_cols, extrasaction="ignore")
        w.writeheader()
        for r in ordered:
            w.writerow({c: r.get(c, "") for c in out_cols})
    tmp.replace(args.out)
    print(f"\nwrote {args.out} ({len(ordered)} rows, {len(out_cols)} cols)")


if __name__ == "__main__":
    main()
