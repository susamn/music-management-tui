#!/usr/bin/env python3
"""wiki-push.py - push per-track wiki directories out to the music collection.

    list        every track directory under $WIKI_DIR that git has NOT
                committed yet. One wiki-relative track path per line, for
                `fzf -m`.
    to-music    copy the picked directories into $MUSIC_DIR so mpdtui can read
                them (overwrite existing after one confirm).
    to-drive    rclone the picked directories to $RCLONE_MUSIC_REMOTE_PATH.

Unlike lyrics, this is not a straight relative-path copy. A track needs a
*folder*, and dropping one folder per track into the album directory would bury
the audio, so the destination gains a wiki/ level:

    wiki/<artist>/<album>/<track>/   ->   <music>/<artist>/<album>/wiki/<track>/

Nothing is moved, staged, committed or deleted - the music-metadata working
tree is untouched.

    wiki-push.py list | fzf -m | wiki-push.py to-music
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

AUDIO_EXTS = (".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav")


def die(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def ask(msg):
    """Prompt on the terminal even when stdin is a pipe (fzf selection)."""
    try:
        if sys.stdin.isatty():
            return input(msg)
        with open("/dev/tty", "r") as tty:
            sys.stderr.write(msg)
            sys.stderr.flush()
            return tty.readline()
    except (OSError, EOFError):
        return ""


def metadata_dir():
    d = os.environ.get("MUSIC_METADATA_DIR")
    if not d:
        die("$MUSIC_METADATA_DIR not set")
    p = Path(d).expanduser()
    if not (p / ".git").exists():
        die(f"not a git repo: {p}")
    return p


def wiki_root(repo):
    d = os.environ.get("WIKI_DIR")
    return Path(d).expanduser() if d else repo / "wiki"


def wiki_rel_name(repo):
    """The wiki root's name relative to the repo, for reading git's output."""
    try:
        return str(wiki_root(repo).relative_to(repo))
    except ValueError:
        return "wiki"


def uncommitted_tracks(repo):
    """Track directories with uncommitted content, wiki-relative.

    git reports files; the unit here is the directory that holds them, because
    a track's story is its wiki.json *and* its images and they are pushed
    together or not at all. -uall so a brand-new directory is listed
    file-by-file rather than collapsed to one entry.
    """
    prefix = wiki_rel_name(repo)
    out = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "-z",
         "--untracked-files=all", "--", prefix],
        capture_output=True, text=True, check=True).stdout
    tracks = set()
    for entry in out.split("\0"):
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "D" in xy:                       # deletion / removal
            continue
        if not path.startswith(prefix + "/"):
            continue
        rel = Path(path[len(prefix) + 1:])
        if rel.parent == Path("."):         # a stray file at the wiki root
            continue
        tracks.add(str(rel.parent))         # the directory holding it
    return sorted(tracks)


def read_rels(stream, prefix):
    rels = []
    for line in stream:
        line = line.strip()
        if not line:
            continue
        if line.startswith(prefix + "/"):
            line = line[len(prefix) + 1:]
        rels.append(line.rstrip("/"))
    return rels


def dest_rel(rel):
    """Where a track directory lands, relative to the music root.

    The one place the two trees differ: <album>/<track>/ becomes
    <album>/wiki/<track>/, so an album keeps one wiki folder rather than
    gaining a directory per track among its audio files.
    """
    p = Path(rel)
    return p.parent / "wiki" / p.name


def has_audio(music, rel):
    """Is there actually a track here? Catches an orphaned or misfiled wiki."""
    d = music / Path(rel).parent
    stem = Path(rel).name
    return any((d / (stem + ext)).is_file() for ext in AUDIO_EXTS)


def cmd_list(args):
    for rel in uncommitted_tracks(metadata_dir()):
        print(rel)


def cmd_to_music(args):
    repo = metadata_dir()
    wiki = wiki_root(repo)
    music = Path(os.environ.get("MUSIC_DIR", "")).expanduser()
    if not music.is_dir():
        die("$MUSIC_DIR not set or not a dir")

    rels = args.rels or read_rels(sys.stdin, wiki_rel_name(repo))
    if not rels:
        die("no tracks given (pipe `wiki-push.py list` through fzf)")

    plan, overwrite, missing, orphan = [], [], [], []
    for rel in rels:
        src = wiki / rel
        if not (src / "wiki.json").is_file():
            missing.append(rel)
            continue
        if not has_audio(music, rel):
            orphan.append(rel)
            continue
        dst = music / dest_rel(rel)
        (overwrite if dst.exists() else plan).append((src, dst, rel))

    for rel in missing:
        print(f"skip (no wiki.json): {rel}", file=sys.stderr)
    for rel in orphan:
        print(f"skip (no audio file there): {rel}", file=sys.stderr)
    for _s, _d, rel in plan:
        print(f"new       {rel}")
    for _s, _d, rel in overwrite:
        print(f"OVERWRITE {rel}")

    todo = plan + overwrite
    if not todo:
        return
    if overwrite:
        a = ask(f"\n{len(overwrite)} already there. Overwrite them? [y/N] ").strip().lower()
        if a != "y":
            todo = plan
            if not todo:
                print("nothing to do", file=sys.stderr)
                return

    n = 0
    for src, dst, rel in todo:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)
        # copytree rather than a file loop: a track's directory is its unit,
        # and a half-copied story (json without its images) is worse than none.
        shutil.copytree(src, dst)
        n += 1
    print(f"copied {n} track(s) into {music}", file=sys.stderr)


def cmd_to_drive(args):
    repo = metadata_dir()
    wiki = wiki_root(repo)
    remote = os.environ.get("RCLONE_MUSIC_REMOTE_PATH")
    if not remote:
        die("$RCLONE_MUSIC_REMOTE_PATH not set")
    if not shutil.which("rclone"):
        die("rclone not on PATH")

    rels = args.rels or read_rels(sys.stdin, wiki_rel_name(repo))
    if not rels:
        die("no tracks given (pipe `wiki-push.py list` through fzf)")

    n = 0
    for rel in rels:
        src = wiki / rel
        if not (src / "wiki.json").is_file():
            print(f"skip (no wiki.json): {rel}", file=sys.stderr)
            continue
        dst = f"{remote}/{dest_rel(rel)}"
        print(f"rclone copy {rel} -> {dst}")
        r = subprocess.run(["rclone", "copy", str(src), dst])
        if r.returncode == 0:
            n += 1
    print(f"pushed {n} track(s) to {remote}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("list", cmd_list), ("to-music", cmd_to_music), ("to-drive", cmd_to_drive)):
        s = sub.add_parser(name)
        s.add_argument("rels", nargs="*", help="wiki-relative track paths (default: stdin)")
        s.set_defaults(fn=fn)
    args = ap.parse_args()
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
