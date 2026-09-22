#!/usr/bin/env python3
"""playlist-writeback.py - apply playlists/*.m3u additions into Apple Music.app.

The missing direction: playlist-sync.py only ever goes Apple Music.app ->
files.csv -> playlists/*.m3u. After a git pull brings in a playlist edited
elsewhere (or the m3u-union merge driver combines two machines' additions),
those new tracks sit in the .m3u but never reach Apple Music.app itself.
This is the reverse: for each track the .m3u wants that the corresponding
Apple Music.app playlist doesn't have yet, add it there.

Matched by MCATALOGID, not persistent ID or fuzzy artist/album/track: each
.m3u line's id comes straight from files.csv; each Apple Music track's id is
read from the tag on the actual file at its `location()` (same approach as
apple-music-tag-sync.py / mcatalogid-backfill.py, duplicated here per this
repo's standalone-script convention). Exact match or a clear "not yet
imported to Apple Music" - never a fuzzy-matching coincidence.

Additive only - never removes a track from an Apple Music.app playlist.
Auto-creates the Apple Music playlist if it doesn't exist yet.

    playlist-writeback.py --one "Rock Hindi" --dry-run
    playlist-writeback.py --one "Rock Hindi"
    playlist-writeback.py --all --dry-run
    playlist-writeback.py --all

Target Apple Music playlist name = the .m3u filename minus extension - the
same sanitization playlist-sync.py's safe_filename() already applies (and
already warns about on collision); no new lossiness introduced here.

Requires mutagen (`pip install --user mutagen`) and bin/writeback.js (the
only Music.app *write* in this repo - see docs/mac-playlist-writeback.md).
Paths from $MUSIC_METADATA_DIR / $FILES_CSV / $APPLE_MUSIC_DIR.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

try:
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4
except ImportError:
    sys.exit("playlist-writeback.py needs mutagen: pip install --user mutagen")

HERE = Path(__file__).resolve().parent
WRITEBACK_JS = HERE / "writeback.js"
_REPO_DEFAULT = os.environ.get("MUSIC_METADATA_DIR") or str(HERE.parent)
_CSV_DEFAULT = os.environ.get("FILES_CSV") or f"{_REPO_DEFAULT}/files.csv"
_APPLE_DEFAULT = os.environ.get("APPLE_MUSIC_DIR") or \
    str(Path.home() / "Music/Music/Media.localized/Music")
TAG_KEY = "mcatalogid"


def is_real(value):
    return bool(value) and value.strip().lower() != "catalog"


def read_tag(path):
    """Same approach as apple-music-tag-sync.py / mcatalogid-backfill.py."""
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            id3 = ID3(path)
            for frame in id3.getall("TXXX"):
                if frame.desc.strip().lower() == TAG_KEY:
                    return str(frame.text[0]) if frame.text else ""
            return None
        if ext == ".m4a":
            tags = MP4(path).tags or {}
            for key, val in tags.items():
                if key.lower().endswith(f":{TAG_KEY}") and key.startswith("----:"):
                    return bytes(val[0]).decode("utf-8", "replace") if val else ""
            return None
    except Exception:
        return None
    return None


def load_csv_mcatalogids(csv_path):
    """files.csv path -> mcatalogid, real values only.

    Keys are NFC-normalized - generate-files-csv.py now writes NFC, but this
    stays defensive against a files.csv generated before that fix (or any
    future source that hands back NFD, which is what macOS/APFS does for
    any path component with a decomposable character)."""
    out = {}
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            v = row.get("mcatalogid", "")
            if is_real(v):
                out[unicodedata.normalize("NFC", row["path"])] = v
    return out


def scan_apple_by_mcatalogid(apple_root):
    """mcatalogid -> absolute path, for every tagged file under apple_root."""
    out = {}
    for path in apple_root.rglob("*"):
        if path.is_file() and path.suffix.lower() in (".mp3", ".m4a"):
            val = read_tag(path)
            if is_real(val):
                out[val] = str(path)
    return out


def read_playlist_locations(playlist_name):
    """Current Apple Music.app locations for a playlist, or None if missing."""
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", str(WRITEBACK_JS), "--read", playlist_name],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"writeback.js --read failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["locations"] if data["found"] else None


def apply_playlist(target_name, to_add_paths):
    req = {"playlist": target_name, "create_if_missing": True, "add": to_add_paths}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(req, fh)
        req_path = fh.name
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", str(WRITEBACK_JS), req_path],
            capture_output=True, text=True,
        )
    finally:
        os.unlink(req_path)
    if result.returncode != 0:
        raise RuntimeError(f"writeback.js apply failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


def plan_for_playlist(m3u_path, csv_ids, apple_by_id):
    target_name = m3u_path.stem
    m3u_lines = [l.strip() for l in m3u_path.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]

    wanted_ids, unresolved = set(), []
    for line in m3u_lines:
        mid = csv_ids.get(unicodedata.normalize("NFC", line))
        if mid:
            wanted_ids.add(mid)
        else:
            unresolved.append(line)

    locations = read_playlist_locations(target_name)
    current_found = locations is not None
    current_ids = set()
    for loc in locations or []:
        mid = read_tag(Path(loc))
        if is_real(mid):
            current_ids.add(mid)

    missing_ids = sorted(wanted_ids - current_ids)
    to_add_paths, not_in_library = [], []
    for mid in missing_ids:
        p = apple_by_id.get(mid)
        (to_add_paths if p else not_in_library).append(p or mid)

    return {
        "target_name": target_name, "current_found": current_found,
        "to_add_paths": to_add_paths, "not_in_library": not_in_library,
        "unresolved": unresolved,
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--one", metavar="NAME",
                        help="one playlist - the .m3u filename, without extension")
    group.add_argument("--all", action="store_true", help="every playlists/*.m3u")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo", default=_REPO_DEFAULT,
                     help="music-metadata dir holding playlists/ (default: $MUSIC_METADATA_DIR)")
    ap.add_argument("--csv", default=_CSV_DEFAULT,
                     help="files.csv path (default: $FILES_CSV)")
    ap.add_argument("--apple-root", default=_APPLE_DEFAULT,
                     help="Apple Music managed copy (default: $APPLE_MUSIC_DIR)")
    args = ap.parse_args()

    csv_path = Path(args.csv).expanduser()
    apple_root = Path(args.apple_root).expanduser()
    playlist_dir = Path(args.repo).expanduser() / "playlists"

    if not csv_path.is_file():
        sys.exit(f"missing {csv_path}")
    if not apple_root.is_dir():
        sys.exit(f"not a directory: {apple_root}")

    if args.one:
        m3u_path = playlist_dir / f"{args.one}.m3u"
        if not m3u_path.is_file():
            sys.exit(f"no such playlist file: {m3u_path}")
        targets = [m3u_path]
    else:
        targets = sorted(playlist_dir.glob("*.m3u"))

    print(f"reading {csv_path.name} ...", file=sys.stderr)
    csv_ids = load_csv_mcatalogids(csv_path)
    print(f"scanning {apple_root} for tagged files ...", file=sys.stderr)
    apple_by_id = scan_apple_by_mcatalogid(apple_root)
    print(f"{len(apple_by_id)} tagged files found under {apple_root.name}\n",
          file=sys.stderr)

    total_added, total_created = 0, 0
    for m3u_path in targets:
        plan = plan_for_playlist(m3u_path, csv_ids, apple_by_id)

        tag = f"{len(plan['to_add_paths']):4d} to add"
        if plan["not_in_library"]:
            tag += f"  {len(plan['not_in_library'])} not in library"
        if plan["unresolved"]:
            tag += f"  {len(plan['unresolved'])} unresolved"
        if not plan["current_found"]:
            tag += "  (new playlist)"
        print(f"  {plan['target_name'][:44]:44s} {tag}")

        for line in plan["unresolved"]:
            print(f"    ⚠ no mcatalogid in files.csv: {line}", file=sys.stderr)
        for mid in plan["not_in_library"]:
            print(f"    ⚠ not yet imported to Apple Music: mcatalogid {mid}",
                  file=sys.stderr)

        if args.dry_run or not plan["to_add_paths"]:
            continue

        result = apply_playlist(plan["target_name"], plan["to_add_paths"])
        total_added += len(result["added"])
        total_created += 1 if result["created"] else 0
        if result["added"]:
            print(f"    wrote {len(result['added'])} track(s)"
                  f"{' (created playlist)' if result['created'] else ''}")
        if result["already_present"]:
            print(f"    ({len(result['already_present'])} already present, skipped)")

    if args.dry_run:
        print("\n--dry-run: nothing written")
    else:
        print(f"\nwrote {total_added} track(s) across {len(targets)} playlist(s), "
              f"{total_created} playlist(s) created")
    return 0


if __name__ == "__main__":
    sys.exit(main())
