#!/usr/bin/env python3
"""apple-music-tag-sync.py - one-time: copy MCATALOGID into Apple Music's own copy.

With "keep library organized" + "copy music files" on in Music.app,
importing a track makes Apple Music's own byte copy under $APPLE_MUSIC_DIR -
tagging the Google Drive copy (bin/mcatalogid-backfill.py) doesn't touch it.
This walks both trees directly (no Music.app / fetch.js needed - a filename
on disk is enough), fuzzy-matches each Apple file onto its Google Drive
counterpart using the same engine as bin/playlist-sync.py, and copies
MCATALOGID across where the Apple side is missing it.

Meant to run once, to catch up the existing library. Going forward: tag
before importing and the tag travels with the copy automatically.

    apple-music-tag-sync.py --dry-run   # report only, write nothing
    apple-music-tag-sync.py             # write for real

A track whose Apple copy already has a *different* real id is left alone and
reported as a conflict - never silently overwritten. Requires mutagen
(`pip install --user mutagen`) - see docs/mcatalogid.md for why not ffmpeg.

Paths from $GDRIVE_MUSIC_DIR / $APPLE_MUSIC_DIR (override: --gdrive --apple).
"""
import argparse
import os
import re
import sys
import unicodedata
from pathlib import Path

try:
    from mutagen.id3 import ID3, TXXX
    from mutagen.mp4 import MP4, MP4FreeForm
except ImportError:
    sys.exit("apple-music-tag-sync.py needs mutagen: pip install --user mutagen")

_GDRIVE_DEFAULT = os.environ.get("GDRIVE_MUSIC_DIR") or ""
_APPLE_DEFAULT = os.environ.get("APPLE_MUSIC_DIR") or \
    str(Path.home() / "Music/Music/Media.localized/Music")
AUDIO_EXT = {".mp3", ".m4a", ".flac"}
TAG_KEY = "mcatalogid"
MP4_FREEFORM_KEY = f"----:com.apple.iTunes:{TAG_KEY}"

# --- matching engine, ported from playlist-sync.py (same algorithm; no
# "prefer" tie-break here since there's no prior output file to stay stable
# against) ------------------------------------------------------------------
_non_slug = re.compile(r"[^a-z0-9@]")
_multi_dash = re.compile(r"-+")
_apple_num = re.compile(r"^\d+-\d+\s+|^\d+\s+")
_apple_num_cap = re.compile(r"^(?:\d+-)?(\d+)\s")
_tree_mid = re.compile(r"-\[mid-.*\]$")
_tree_num = re.compile(r"^\d+-\d+-|^\d+-")
_tree_num_cap = re.compile(r"^(?:\d+-)?(\d+)-")


def normalize(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s).lower()
    s = _multi_dash.sub("-", _non_slug.sub("-", s))
    return s.strip("-")


def clean_apple_name(filename):
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    return normalize(_apple_num.sub("", name))


def clean_tree_name(filename):
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    name = _tree_mid.sub("", name)
    return normalize(_tree_num.sub("", name))


def apple_track_num(filename):
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    m = _apple_num_cap.match(name)
    return int(m.group(1)) if m else None


def tree_track_num(filename):
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    name = _tree_mid.sub("", name)
    m = _tree_num_cap.match(name)
    return int(m.group(1)) if m else None


def build_lookups(tree_paths):
    art_alb_trk, art_trk = {}, {}
    for full in tree_paths:
        parts = full.split("/")
        if len(parts) >= 3:
            key = f"{normalize(parts[0])}|{normalize(parts[1])}|{clean_tree_name(parts[-1])}"
            art_alb_trk.setdefault(key, []).append(full)
        if len(parts) >= 2:
            key = f"{normalize(parts[0])}|{clean_tree_name(parts[-1])}"
            art_trk.setdefault(key, []).append(full)
    return art_alb_trk, art_trk


def find_match(apple_path, art_alb_trk, art_trk):
    parts = apple_path.split("/")
    if len(parts) < 3:
        return None
    artist = normalize(parts[-3])
    album = normalize(parts[-2])
    track = clean_apple_name(parts[-1])
    apnum = apple_track_num(parts[-1])
    for cands in (art_alb_trk.get(f"{artist}|{album}|{track}"),
                  art_trk.get(f"{artist}|{track}")):
        if not cands:
            continue
        if len(cands) > 1 and apnum is not None:
            numbered = [c for c in cands if tree_track_num(c.rsplit("/", 1)[-1]) == apnum]
            if len(numbered) == 1:
                return numbered[0]
        return cands[0]
    return None


# --- tag read/write, same approach as mcatalogid-backfill.py --------------
def is_real(value):
    return bool(value) and value.strip().lower() != "catalog"


def read_tag(path):
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
    except Exception as e:
        return f"__ERROR__:{e}"
    return None


def write_tag(path, value):
    ext = path.suffix.lower()
    value = str(value)
    if ext == ".mp3":
        id3 = ID3(path)
        id3.setall("TXXX", [f for f in id3.getall("TXXX")
                             if f.desc.strip().lower() != TAG_KEY])
        id3.add(TXXX(encoding=3, desc=TAG_KEY, text=[value]))
        id3.save(path)
    elif ext == ".m4a":
        mp4 = MP4(path)
        for key in [k for k in list(mp4.tags or {})
                    if k.lower().endswith(f":{TAG_KEY}") and k.startswith("----:")]:
            del mp4.tags[key]
        mp4.tags[MP4_FREEFORM_KEY] = [MP4FreeForm(value.encode("utf-8"))]
        mp4.save()
    else:
        raise ValueError(f"unsupported extension: {ext}")


def walk(root):
    """Relative POSIX 'Artist/Album/File.ext' path for every audio file under root."""
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in AUDIO_EXT:
            yield path, str(path.relative_to(root))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--gdrive", default=_GDRIVE_DEFAULT,
                     help="Google Drive music dir (default: $GDRIVE_MUSIC_DIR)")
    ap.add_argument("--apple", default=_APPLE_DEFAULT,
                     help="Apple Music managed copy (default: $APPLE_MUSIC_DIR)")
    args = ap.parse_args()

    if not args.gdrive:
        sys.exit("no --gdrive and $GDRIVE_MUSIC_DIR is unset")
    gdrive_root = Path(args.gdrive).expanduser()
    apple_root = Path(args.apple).expanduser()
    for root, name in ((gdrive_root, "gdrive"), (apple_root, "apple")):
        if not root.is_dir():
            sys.exit(f"not a directory ({name}): {root}")

    print(f"indexing {gdrive_root} ...", file=sys.stderr)
    gdrive_paths = [rel for _, rel in walk(gdrive_root)]
    art_alb_trk, art_trk = build_lookups(gdrive_paths)
    print(f"{len(gdrive_paths)} files indexed", file=sys.stderr)

    print(f"scanning {apple_root} ...", file=sys.stderr)
    apple_files = list(walk(apple_root))
    print(f"{len(apple_files)} Apple Music files found", file=sys.stderr)

    plan, conflicts, no_source_id, unmatched, already_ok = [], [], 0, 0, 0
    gdrive_tag_cache = {}

    def gdrive_tag(rel):
        if rel not in gdrive_tag_cache:
            gdrive_tag_cache[rel] = read_tag(gdrive_root / rel)
        return gdrive_tag_cache[rel]

    for apple_path, apple_rel in apple_files:
        match_rel = find_match(apple_rel, art_alb_trk, art_trk)
        if match_rel is None:
            unmatched += 1
            continue
        source_val = gdrive_tag(match_rel)
        if isinstance(source_val, str) and source_val.startswith("__ERROR__:"):
            continue
        if not is_real(source_val):
            no_source_id += 1
            continue
        apple_val = read_tag(apple_path)
        if isinstance(apple_val, str) and apple_val.startswith("__ERROR__:"):
            continue
        if is_real(apple_val):
            if apple_val != source_val:
                conflicts.append((apple_rel, apple_val, match_rel, source_val))
            else:
                already_ok += 1
            continue
        plan.append((apple_path, apple_rel, source_val, match_rel))

    print(f"\nalready correct:          {already_ok}", file=sys.stderr)
    print(f"to write:                 {len(plan)}", file=sys.stderr)
    print(f"unmatched (no gdrive counterpart): {unmatched}", file=sys.stderr)
    print(f"matched but source has no real id: {no_source_id}", file=sys.stderr)
    print(f"conflicts (left alone):   {len(conflicts)}", file=sys.stderr)
    for apple_rel, apple_val, match_rel, source_val in conflicts:
        print(f"  ⚠ {apple_rel}: apple={apple_val} gdrive[{match_rel}]={source_val}",
              file=sys.stderr)

    for _, apple_rel, value, match_rel in plan:
        print(f"write\t{value}\t{apple_rel}\t(from {match_rel})")

    if args.dry_run:
        print(f"\n--dry-run: {len(plan)} file(s) would be tagged, nothing written",
              file=sys.stderr)
        return 0

    written, failed = 0, 0
    for apple_path, apple_rel, value, match_rel in plan:
        try:
            write_tag(apple_path, value)
            readback = read_tag(apple_path)
            if readback != value:
                raise ValueError(f"readback mismatch: wrote {value!r}, read {readback!r}")
            written += 1
        except Exception as e:
            print(f"  ⚠ failed: {apple_rel}: {e}", file=sys.stderr)
            failed += 1

    print(f"\nwrote {written}, failed {failed}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
