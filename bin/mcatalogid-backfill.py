#!/usr/bin/env python3
"""mcatalogid-backfill.py - fill in a missing MCATALOGID tag.

MCATALOGID is a custom tag the user writes by hand so a track stays
identifiable (for playlist regrouping) even after the file is moved or
renamed. This script never invents an ID for a track that already has a
real one, and never touches audio data - only the tag block, via mutagen
(ffmpeg's `-c copy` remux was tested and found to silently truncate some
real files; mutagen edits tags in place and is immune to that).

Two passes, in order:

  1. backfill - the file has no real MCATALOGID, but its filename already
     carries one ("...-[mid-1012747].m4a" -> 1012747). Use it as-is.
  2. assign   - the file still has no real MCATALOGID after pass 1 (this
     also covers the "catalog" placeholder value some tracks carry, which
     means "no real ID was ever assigned", same as having none at all).
     Assign the next unused integer after the highest MCATALOGID found
     anywhere in the tree (in a tag OR a filename), one per file, in a
     stable (path-sorted) order. The filename is never renamed.

A file whose tag and filename both carry a real but *different* id is left
untouched and reported as a conflict - never silently overwritten.

    mcatalogid-backfill.py --dry-run   # report only, write nothing
    mcatalogid-backfill.py             # write for real
    mcatalogid-backfill.py --root DIR  # override $GDRIVE_MUSIC_DIR

Requires mutagen (`pip install mutagen`) - stdlib alone can't safely patch
an MP4 freeform atom or an ID3 frame in place. Root dir from --root or
$GDRIVE_MUSIC_DIR.
"""
import argparse
import os
import re
import sys
from pathlib import Path

try:
    from mutagen.id3 import ID3, TXXX
    from mutagen.mp4 import MP4, MP4FreeForm
    from mutagen.flac import FLAC
except ImportError:
    sys.exit("mcatalogid-backfill.py needs mutagen: pip install --user mutagen")

_ROOT_DEFAULT = os.environ.get("GDRIVE_MUSIC_DIR") or ""
_FILENAME_ID = re.compile(r"-\[mid-(\d+)\]\.\w+$", re.I)
EXTS = {".mp3", ".m4a", ".flac"}
TAG_KEY = "mcatalogid"
MP4_FREEFORM_KEY = f"----:com.apple.iTunes:{TAG_KEY}"


def is_real(value):
    return bool(value) and value.strip().lower() != "catalog"


def read_tag(path):
    """Current MCATALOGID value as a string, or None."""
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
                    raw = val[0]
                    return bytes(raw).decode("utf-8", "replace") if val else ""
            return None
        if ext == ".flac":
            f = FLAC(path)
            vals = f.get(TAG_KEY)
            return vals[0] if vals else None
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
    elif ext == ".flac":
        f = FLAC(path)
        f[TAG_KEY] = [value]
        f.save()
    else:
        raise ValueError(f"unsupported extension: {ext}")


def scan(root):
    """Yield (path, current_value_or_None, filename_id_or_None) for every audio file."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in EXTS:
            continue
        current = read_tag(path)
        m = _FILENAME_ID.search(path.name)
        filename_id = m.group(1) if m else None
        yield path, current, filename_id


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true",
                     help="report tagged/missing counts only, write nothing, "
                          "no per-file plan")
    ap.add_argument("--root", default=_ROOT_DEFAULT,
                     help="music dir to scan (default: $GDRIVE_MUSIC_DIR)")
    args = ap.parse_args()

    if not args.root:
        sys.exit("no root: pass --root or set $GDRIVE_MUSIC_DIR")
    root = Path(args.root).expanduser()
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")

    print(f"scanning {root} ...", file=sys.stderr)
    rows = list(scan(root))
    print(f"{len(rows)} audio files found", file=sys.stderr)

    errors = [(p, v) for p, v, _ in rows if isinstance(v, str) and v.startswith("__ERROR__:")]
    for p, v in errors:
        print(f"  ⚠ read error, skipping: {p.relative_to(root)}: {v[10:]}", file=sys.stderr)

    # global max across every real tag value AND every filename-embedded id,
    # so a brand-new assignment can never collide with either.
    global_max = 0
    for _, current, filename_id in rows:
        for candidate in (current if is_real(current) else None, filename_id):
            if candidate and candidate.isdigit():
                global_max = max(global_max, int(candidate))

    backfill, assign, conflicts, already_ok = [], [], [], 0
    by_ext = {}  # ext -> [tagged, missing]
    for path, current, filename_id in rows:
        if isinstance(current, str) and current.startswith("__ERROR__:"):
            continue
        ext = path.suffix.lower()
        counts = by_ext.setdefault(ext, [0, 0])
        if is_real(current):
            if filename_id and current != filename_id:
                conflicts.append((path, current, filename_id))
            else:
                already_ok += 1
                counts[0] += 1
            continue
        counts[1] += 1
        if filename_id:
            backfill.append((path, filename_id))
        else:
            assign.append(path)  # value decided below, in stable path order

    if args.status:
        rows_out = [
            ("total audio files:", len(rows)),
            ("tagged (real id):", already_ok),
            ("missing:", len(backfill) + len(assign)),
            ("  backfillable from filename:", len(backfill)),
            ("  needs a brand-new id:", len(assign)),
            ("conflicts (tag != filename):", len(conflicts)),
        ]
        width = max(len(label) for label, _ in rows_out) + 2
        print(f"\nMCATALOGID status for {root}\n")
        for label, value in rows_out:
            print(f"{label:<{width}}{value}")
        print()
        for ext in sorted(by_ext):
            tagged, missing = by_ext[ext]
            total = tagged + missing
            pct = f"{100 * tagged / total:.1f}%" if total else "n/a"
            print(f"  {ext:6}{tagged:6} tagged / {total:6} total  ({pct})")
        return 0

    next_id = global_max + 1
    assign_plan = []
    for path in assign:
        assign_plan.append((path, str(next_id)))
        next_id += 1

    print(f"\nalready tagged (real id): {already_ok}", file=sys.stderr)
    print(f"backfill from filename:   {len(backfill)}", file=sys.stderr)
    print(f"assign new id:            {len(assign_plan)}  "
          f"(range {global_max + 1}..{next_id - 1})" if assign_plan else
          "assign new id:            0", file=sys.stderr)
    print(f"conflicts (tag != filename, left alone): {len(conflicts)}", file=sys.stderr)
    if conflicts:
        for path, current, filename_id in conflicts:
            print(f"  ⚠ {path.relative_to(root)}: tag={current} filename={filename_id}",
                  file=sys.stderr)

    plan = [(path, "backfill", value) for path, value in backfill] + \
           [(path, "assign", value) for path, value in assign_plan]

    for path, action, value in plan:
        print(f"{action}\t{value}\t{path.relative_to(root)}")

    if args.dry_run:
        print(f"\n--dry-run: {len(plan)} file(s) would be tagged, nothing written",
              file=sys.stderr)
        return 0

    written, failed = 0, 0
    for path, action, value in plan:
        try:
            write_tag(path, value)
            readback = read_tag(path)
            if readback != value:
                raise ValueError(f"readback mismatch: wrote {value!r}, read {readback!r}")
            written += 1
        except Exception as e:
            print(f"  ⚠ failed: {path.relative_to(root)}: {e}", file=sys.stderr)
            failed += 1

    print(f"\nwrote {written}, failed {failed}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
