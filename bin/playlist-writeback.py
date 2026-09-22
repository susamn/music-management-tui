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


def scan_apple_by_mcatalogid(apple_root, library_locations=None):
    """Index paths by ID; retain duplicate candidates for playlist-level checks."""
    out = {}
    for path in apple_root.rglob("*"):
        if path.is_file() and path.suffix.lower() in (".mp3", ".m4a"):
            if library_locations is not None and path.resolve() not in library_locations:
                continue
            val = read_tag(path)
            if is_real(val):
                out.setdefault(val, []).append(str(path))
    return out


def read_library_locations():
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", str(WRITEBACK_JS), "--library"],
        capture_output=True, text=True, check=True,
    )
    return {Path(p).resolve() for p in json.loads(result.stdout)["locations"]}


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

    wanted_ids, unresolved = {}, []
    for line in m3u_lines:
        mid = csv_ids.get(unicodedata.normalize("NFC", line))
        if mid:
            wanted_ids[mid] = None
        else:
            unresolved.append(line)

    locations = read_playlist_locations(target_name)
    current_found = locations is not None
    current_ids = set()
    for loc in locations or []:
        mid = read_tag(Path(loc))
        if is_real(mid):
            current_ids.add(mid)

    missing_ids = [mid for mid in wanted_ids if mid not in current_ids]
    to_add_paths, not_in_library, ambiguous = [], [], {}
    for mid in missing_ids:
        candidates = apple_by_id.get(mid, [])
        if len(candidates) > 1:
            ambiguous[mid] = sorted(candidates)
        elif candidates:
            to_add_paths.append(candidates[0])
        else:
            not_in_library.append(mid)

    return {
        "target_name": target_name, "current_found": current_found,
        "to_add_paths": to_add_paths, "not_in_library": not_in_library,
        "unresolved": unresolved, "ambiguous": ambiguous,
        "apple_count": len(locations or []), "exported_count": len(m3u_lines),
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--one", metavar="NAME",
                        help="one playlist - the .m3u filename, without extension")
    group.add_argument("--all", action="store_true", help="every playlists/*.m3u")
    group.add_argument("--pick", action="store_true", help="choose a playlist using fzf")
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

    if args.pick:
        names = sorted(p.stem for p in playlist_dir.glob("*.m3u") if p.is_file())
        if not names:
            print(f"No playlists found in {playlist_dir}")
            return 0
        try:
            selection = subprocess.run(
                ["fzf", "--read0", "--print0", "--no-multi", "--prompt=Playlist > "],
                input="\0".join(names) + "\0", stdout=subprocess.PIPE, text=True,
            )
        except FileNotFoundError:
            sys.exit("Playlist selection requires fzf. Install it and try again.")
        if selection.returncode in (1, 130):
            return 0
        if selection.returncode != 0:
            sys.exit(f"fzf exited with code {selection.returncode}")
        args.one = selection.stdout.removesuffix("\0")
        if args.one not in names:
            sys.exit("fzf returned an unknown playlist")

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
    apple_by_id = scan_apple_by_mcatalogid(apple_root, read_library_locations())
    print(f"{len(apple_by_id)} tagged files found under {apple_root.name}\n",
          file=sys.stderr)

    total_added, total_created = 0, 0
    planned, unchanged, blocked = 0, 0, 0
    for m3u_path in targets:
        plan = plan_for_playlist(m3u_path, csv_ids, apple_by_id)

        has_errors = bool(plan["not_in_library"] or plan["unresolved"] or plan.get("ambiguous"))
        changes = bool(plan["to_add_paths"]) or not plan["current_found"]
        kind = "Existing playlist" if plan["current_found"] else "New playlist"
        print(f"\n{kind}: {plan['target_name']}")
        print(f"  Apple Music: {plan['apple_count']} local tracks")
        print(f"  Exported file: {plan['exported_count']} tracks")
        additions = 0 if has_errors else len(plan["to_add_paths"])
        print(f"  Apple Music changes: add {additions}, delete 0 (additive writeback)")
        if has_errors:
            print("  Blocked: resolve the tracks listed below before applying.")

        for line in plan["unresolved"]:
            print(f"    ⚠ no mcatalogid in files.csv: {line}", file=sys.stderr)
        for mid in plan["not_in_library"]:
            print(f"    ⚠ not yet imported to Apple Music: mcatalogid {mid}",
                  file=sys.stderr)
        for mid, paths in plan.get("ambiguous", {}).items():
            print(f"    ⚠ multiple library files have MCATALOGID {mid}; "
                  "no candidate selected:", file=sys.stderr)
            for path in paths:
                print(f"      {path}", file=sys.stderr)

        if has_errors:
            blocked += 1
            continue
        if not changes:
            unchanged += 1
            continue
        planned += 1
        if args.dry_run:
            continue

        result = apply_playlist(plan["target_name"], plan["to_add_paths"])
        total_added += len(result["added"])
        total_created += 1 if result["created"] else 0
        if result["not_in_library"]:
            blocked += 1
            for path in result["not_in_library"]:
                print(f"    ERROR no longer in Apple Music library: {path}", file=sys.stderr)
        if result["added"]:
            print(f"    wrote {len(result['added'])} track(s)"
                  f"{' (created playlist)' if result['created'] else ''}")
        if result["already_present"]:
            print(f"    ({len(result['already_present'])} already present, skipped)")

    if not args.dry_run:
        print(f"\nwrote {total_added} track(s) across {len(targets)} playlist(s), "
              f"{total_created} playlist(s) created")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
