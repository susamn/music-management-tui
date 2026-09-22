#!/usr/bin/env python3
"""sync.py - regenerate playlists/*.m3u from Apple Music.app.

Replaces the old PlaylistSyncManager.app. Pulls every user playlist live from
Music.app (via fetch.js / JXA), fuzzy-matches each track's file path onto this
repo's slugified namespace (files.tree), and writes one clean

    playlists/<playlist name>.m3u

per playlist - `#EXTM3U` header, then repo-relative slug paths.

    python3 playlist-sync/sync.py              # regenerate in place
    python3 playlist-sync/sync.py --dry-run    # report only, write nothing
    python3 playlist-sync/sync.py --prune      # also delete .m3u files with no
                                               # matching Music.app playlist
    python3 playlist-sync/sync.py --misses     # print every unmatched track
    python3 playlist-sync/sync.py --from FILE  # read a fetch.js dump (JSON or
                                               # NDJSON; '-' = stdin)

Progress: fetch.js prints "[ 47/213] <playlist name>  (52 tracks)" to stderr as
it reads each playlist, so the ~2-3 min Music.app pass is not a silent wait. For
a real progress bar:  fetch.js --ndjson | pv -l | sync.py --from -

De-duping: a track is written once per playlist even if Music.app lists it
twice, or if two different Music tracks resolve to the same repo file, or if
Music has two playlists with the same name (their tracks are merged). The run
summary reports how many duplicates were dropped.

Matching (ported from the Swift tool, + an NFC fold):
  normalize          NFC, lowercase, every char outside [a-z0-9@] -> "-", collapse
  apple track name   drop extension, strip leading "D-NN " or "NN "
  tree filename      drop extension, strip "-[mid-...]" suffix and "D-NN-"/"NN-"
  lookups            artist|album|track  then  artist|track   (album dropped)
  tie-break          keep the copy already in that .m3u, else files.tree order

macOS + Music.app required (unless --from). Stdlib only.

Paths come from $MUSIC_METADATA_DIR / $FILES_TREE (override: --repo / --tree).
Was music-metadata/playlist-sync/sync.py.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
FETCH = HERE / "fetch.js"
_REPO_DEFAULT = os.environ.get("MUSIC_METADATA_DIR") or str(HERE.parent)
_TREE_DEFAULT = os.environ.get("FILES_TREE") or f"{_REPO_DEFAULT}/files.tree"
AUDIO_EXT = {"mp3", "m4a", "flac", "wav", "ogg", "opus", "m4p", "aac"}

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
    # macOS hands back NFD paths; files.tree is NFC - fold to NFC first so
    # "Fuzön" (o + combining diaeresis) and "fuzön" (ö) normalise the same.
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
    """The track number an Apple filename starts with ('1-02 Foo.mp3' -> '02'), or None."""
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    m = _apple_num_cap.match(name)
    return m.group(1) if m else None


def tree_track_num(filename):
    """The track number a tree filename starts with ('02-foo-[mid-1].mp3' -> '02'), or None."""
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    name = _tree_mid.sub("", name)
    m = _tree_num_cap.match(name)
    return m.group(1) if m else None


def parse_tree(path):
    """Yield every audio-file path from a `tree`-format dump."""
    if not path.exists():
        sys.exit(f"missing {path} - the slug namespace to match into")
    stack = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = re.search(r"[├└]── ", raw)
        if not m:
            continue
        depth = m.start() // 4
        name = raw[m.end():].strip()
        del stack[depth:]
        stack.append(name)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in AUDIO_EXT:
            yield "/".join(stack)


def build_lookups(tree_paths):
    """Each key -> list of matching tree paths, in files.tree order."""
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


def find_match(apple_path, art_alb_trk, art_trk, prefer=frozenset()):
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
            # same title appears twice on the album (e.g. reprise, two singers) -
            # the track number is the only thing that tells them apart
            numbered = [c for c in cands if tree_track_num(c.rsplit("/", 1)[-1]) == apnum]
            if len(numbered) == 1:
                return numbered[0]
        for c in cands:                 # keep the copy already in the playlist
            if c in prefer:
                return c
        return cands[0]                 # else first in files.tree order (stable)
    return None


def _parse_dump(text):
    """Accept either a single JSON array or NDJSON (one object per line)."""
    text = text.strip()
    if not text:
        return []
    if text[0] == "[":
        return json.loads(text)
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def fetch_playlists(from_file):
    if from_file:
        text = sys.stdin.read() if from_file == "-" \
            else Path(from_file).read_text(encoding="utf-8")
        return _parse_dump(text)
    if not FETCH.exists():
        sys.exit(f"missing {FETCH}")
    print("fetching playlists from Music.app (a few minutes; names stream below)...",
          file=sys.stderr)
    try:
        # stderr is left attached to the terminal so fetch.js's per-playlist
        # progress line shows live.
        p = subprocess.run(["osascript", "-l", "JavaScript", str(FETCH)],
                           stdout=subprocess.PIPE, text=True, check=True)
    except FileNotFoundError:
        sys.exit("osascript not found - this needs macOS (or use --from).")
    except subprocess.CalledProcessError as e:
        sys.exit(f"fetch.js exited {e.returncode}")
    return _parse_dump(p.stdout)


def safe_filename(name):
    return name.replace("/", "-").replace(":", "-")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", action="store_true",
                    help="delete playlists/*.m3u with no matching Music playlist")
    ap.add_argument("--misses", action="store_true",
                    help="list every unmatched track")
    ap.add_argument("--allow-empty", action="store_true",
                    help="overwrite an existing playlist even when 0 tracks matched "
                         "(default: keep the existing file and warn)")
    ap.add_argument("--from", dest="from_file", metavar="FILE",
                    help="read a fetch.js dump (JSON array or NDJSON) instead of "
                         "querying Music.app; '-' reads stdin, so you can do "
                         "`fetch.js --ndjson | pv -l | sync.py --from -`")
    ap.add_argument("--repo", default=_REPO_DEFAULT,
                    help="music-metadata dir holding playlists/ (default: $MUSIC_METADATA_DIR)")
    ap.add_argument("--tree", default=_TREE_DEFAULT,
                    help="files.tree slug namespace (default: $FILES_TREE)")
    args = ap.parse_args()

    TREE = Path(args.tree).expanduser()
    PLAYLIST_DIR = Path(args.repo).expanduser() / "playlists"

    tree_paths = list(parse_tree(TREE))
    art_alb_trk, art_trk = build_lookups(tree_paths)
    print(f"namespace: {len(tree_paths)} files from {TREE.name}")

    playlists = fetch_playlists(args.from_file)
    print(f"playlists: {len(playlists)} from "
          f"{'Music.app' if not args.from_file else args.from_file}\n")

    def existing_lines(path):
        if not path.exists():
            return None
        return [l for l in path.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")]

    PLAYLIST_DIR.mkdir(exist_ok=True)
    written, skipped, total_matched, total_missed, total_dups = 0, 0, 0, 0, 0
    kept_files = set()
    all_misses = []
    warnings = []

    # Music.app can hold several playlists with the same name; merge them into
    # one .m3u (union of tracks, deduped below).
    merged = {}
    for pl in playlists:
        nm = pl["name"]
        if not nm.strip():
            continue
        merged.setdefault(nm, []).extend(pl["tracks"])

    for name in sorted(merged, key=str.lower):
        raw_tracks = merged[name]
        fname = safe_filename(name) + ".m3u"
        if fname in kept_files:
            warnings.append(f"{name}: filename {fname!r} collides with a different "
                            "playlist name - one overwrites the other")
        dest = PLAYLIST_DIR / fname
        kept_files.add(fname)
        prior = existing_lines(dest)
        prefer = frozenset(prior or ())
        was = None if prior is None else len(prior)

        # de-dupe the Music.app track list first (it can list a track twice,
        # and same-named playlists were just unioned)
        ap_paths, ap_seen = [], set()
        for p in raw_tracks:
            if p not in ap_seen:
                ap_seen.add(p)
                ap_paths.append(p)

        seen, lines, missed = set(), ["#EXTM3U"], set()
        dups = 0
        for ap_path in ap_paths:
            hit = find_match(ap_path, art_alb_trk, art_trk, prefer)
            if hit is None:
                missed.add(ap_path)
            elif hit in seen:
                dups += 1          # two different Music tracks -> same repo file
            else:
                seen.add(hit)
                lines.append(hit)
        dups += len(raw_tracks) - len(ap_paths)   # exact dup rows in Music
        total_matched += len(seen)
        total_missed += len(missed)
        total_dups += dups
        all_misses += [(name, m) for m in sorted(missed)]

        delta = ""
        if was is not None and was != len(seen):
            delta = f"  (was {was})"
            if len(seen) < was * 0.5 and was >= 8:
                warnings.append(f"{name}: {was} -> {len(seen)} tracks")
        tag = f"{len(seen):4d} matched"
        if missed:
            tag += f"  {len(missed)} missed"
        if dups:
            tag += f"  {dups} dup{'s' if dups > 1 else ''} dropped"
        print(f"  {name[:44]:44s} {tag}{delta}")

        # never silently blank a real playlist
        if len(seen) == 0 and was:
            if not args.allow_empty:
                warnings.append(f"{name}: 0 matched, kept existing {was}-track file "
                                "(--allow-empty to overwrite)")
                skipped += 1
                continue

        if not args.dry_run:
            dest.write_text("\n".join(lines), encoding="utf-8")
            written += 1

    orphans = sorted(p.name for p in PLAYLIST_DIR.glob("*.m3u")
                     if p.name not in kept_files)
    print(f"\nmatched {total_matched} unique tracks, missed {total_missed}, "
          f"dropped {total_dups} duplicate(s)")
    if orphans:
        print(f"\n{len(orphans)} .m3u file(s) with no Music.app playlist:")
        for o in orphans:
            print(f"  {o}")
        if args.prune and not args.dry_run:
            for o in orphans:
                (PLAYLIST_DIR / o).unlink()
            print(f"  pruned {len(orphans)}")
        elif orphans:
            print("  (left in place; pass --prune to delete)")

    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  ⚠ {w}")

    if args.misses and all_misses:
        print(f"\n--- {len(all_misses)} unmatched tracks ---")
        for pname, m in all_misses:
            print(f"  [{pname}] {m}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
    else:
        print(f"\nwrote {written} playlist file(s), skipped {skipped}, to {PLAYLIST_DIR}")


if __name__ == "__main__":
    main()
