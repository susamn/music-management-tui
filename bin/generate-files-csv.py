#!/usr/bin/env python3
"""generate-files-csv.py - snapshot a music dir into files.csv.

Replaces files.tree (a tree-drawing-character text file, previously
regenerated only on a separate machine - this repo had no script that
produced it). files.csv is a plain CSV any machine can produce from
whatever local music directory it has, via `music-tui` itself.

    generate-files-csv.py             # regenerate $FILES_CSV from $GDRIVE_MUSIC_DIR
    generate-files-csv.py --dry-run   # report the count only, write nothing
    generate-files-csv.py --root DIR  # scan a different directory
    generate-files-csv.py --out FILE  # write somewhere other than $FILES_CSV

Full regeneration every run - this is a snapshot, not cumulative history
(unlike play_stats.csv, nothing here is merged with a previous run). Rows
sorted by path for stable diffs.

Columns: path,artist,album,filename,ext,mcatalogid

`path` is the exact slug-path format bin/playlist-sync.py's matching engine
already expects (artist/album/file-[mid-N].ext) - swapping files.tree for
files.csv changes only how that namespace is loaded, not how it's matched.
`mcatalogid` is read via mutagen so playlist-sync (and anything else) can
eventually match by stable id, not just fuzzy artist/album/track - blank if
the tag is missing or still the "catalog" placeholder.

Requires mutagen (`pip install --user mutagen`) - see docs/mcatalogid.md.
"""
import argparse
import csv
import os
import sys
import unicodedata
from pathlib import Path

try:
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
    from mutagen.flac import FLAC
except ImportError:
    sys.exit("generate-files-csv.py needs mutagen: pip install --user mutagen")

_ROOT_DEFAULT = os.environ.get("GDRIVE_MUSIC_DIR") or ""
if os.environ.get("FILES_CSV"):
    _OUT_DEFAULT = Path(os.environ["FILES_CSV"]).expanduser()
elif os.environ.get("MUSIC_METADATA_DIR"):
    _OUT_DEFAULT = Path(os.environ["MUSIC_METADATA_DIR"]).expanduser() / "files.csv"
else:
    _OUT_DEFAULT = Path(__file__).resolve().parent.parent / "files.csv"

EXTS = {".mp3", ".m4a", ".flac"}
TAG_KEY = "mcatalogid"


def read_mcatalogid(path):
    """The real MCATALOGID value, or "" if missing/the "catalog" placeholder."""
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            id3 = ID3(path)
            for frame in id3.getall("TXXX"):
                if frame.desc.strip().lower() == TAG_KEY:
                    value = str(frame.text[0]) if frame.text else ""
                    break
            else:
                value = ""
        elif ext == ".m4a":
            tags = MP4(path).tags or {}
            value = ""
            for key, val in tags.items():
                if key.lower().endswith(f":{TAG_KEY}") and key.startswith("----:"):
                    value = bytes(val[0]).decode("utf-8", "replace") if val else ""
                    break
        elif ext == ".flac":
            vals = FLAC(path).get(TAG_KEY)
            value = vals[0] if vals else ""
        else:
            value = ""
    except Exception:
        value = ""
    return "" if value.strip().lower() in ("", "catalog") else value


def scan(root):
    """Yield one row dict per audio file under root."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in EXTS:
            continue
        # macOS/APFS hands back NFD for any filename with a decomposable
        # character (accents etc) - fold to NFC so `path` always matches the
        # form playlists/*.m3u and everything else already uses (same fix
        # playlist-sync.py's normalize() applies for its own match keys; this
        # is the raw stored path, not just a lookup key, so it has to be
        # right here at the source).
        rel = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
        parts = rel.split("/")
        yield {
            "path": rel,
            "artist": parts[0] if len(parts) >= 2 else "",
            "album": parts[1] if len(parts) >= 3 else "",
            "filename": parts[-1],
            "ext": path.suffix.lower().lstrip("."),
            "mcatalogid": read_mcatalogid(path),
        }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root", default=_ROOT_DEFAULT,
                     help="music dir to scan (default: $GDRIVE_MUSIC_DIR)")
    ap.add_argument("--out", default=str(_OUT_DEFAULT),
                     help="output CSV path (default: $FILES_CSV)")
    args = ap.parse_args()

    if not args.root:
        sys.exit("no root: pass --root or set $GDRIVE_MUSIC_DIR")
    root = Path(args.root).expanduser()
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")
    out = Path(args.out).expanduser()

    print(f"scanning {root} ...", file=sys.stderr)
    rows = sorted(scan(root), key=lambda r: r["path"])
    tagged = sum(1 for r in rows if r["mcatalogid"])
    print(f"{len(rows)} audio files found, {tagged} with a real MCATALOGID",
          file=sys.stderr)

    if args.dry_run:
        print(f"\n--dry-run: would write {len(rows)} row(s) to {out}", file=sys.stderr)
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["path", "artist", "album", "filename",
                                            "ext", "mcatalogid"])
        w.writeheader()
        for row in rows:
            w.writerow(row)
    tmp.replace(out)
    print(f"\nwrote {out} ({len(rows)} rows)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
