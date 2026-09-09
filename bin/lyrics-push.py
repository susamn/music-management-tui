#!/usr/bin/env python3
"""lyrics-push.py - push not-yet-committed lyrics out for testing.

`list` finds every .txt/.lrc/.elrc under $MUSIC_METADATA_DIR/lyrics that git
has NOT committed yet - freshly fetched or hand-edited, staged or not (excludes
lrc-sync's *.old backups and deletions). One path per line, relative to
lyrics/, ready to pipe through `fzf -m`.

`to-music` copies the picked files next to the mp3 in the local music
collection ($MUSIC_DIR/<same nested path>) so mpd / mpdtui pick them up and you
can hear whether the sync is any good. Existing sidecars are overwritten after
one confirm.

`to-drive` uploads the picked files to Google Drive via rclone, to
$RCLONE_MUSIC_REMOTE_PATH/<same nested path>.

Nothing is ever moved, staged, committed or deleted - the music-metadata
working tree is untouched.

    lyrics-push.py list
    lyrics-push.py list | fzf -m | lyrics-push.py to-music
    lyrics-push.py list | fzf -m | lyrics-push.py to-drive --dry-run
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

LYRICS_EXTS = (".txt", ".lrc", ".elrc")


def die(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def metadata_dir():
    d = os.environ.get("MUSIC_METADATA_DIR")
    if not d:
        die("$MUSIC_METADATA_DIR not set")
    p = Path(d).expanduser()
    if not (p / ".git").exists():
        die(f"not a git repo: {p}")
    return p


def uncommitted_lyrics(repo):
    out = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "-z", "--", "lyrics"],
        capture_output=True, text=True, check=True).stdout
    rels = []
    for entry in out.split("\0"):
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "D" in xy:                      # deletion / removal
            continue
        if not path.startswith("lyrics/"):
            continue
        if not path.endswith(LYRICS_EXTS):
            continue
        if ".old" in Path(path).name:      # lrc-sync backups
            continue
        rels.append(path[len("lyrics/"):])
    return sorted(set(rels))


def read_rels(stream):
    rels = []
    for line in stream:
        line = line.strip()
        if line:
            rels.append(line[len("lyrics/"):] if line.startswith("lyrics/") else line)
    return rels


def cmd_list(args):
    for rel in uncommitted_lyrics(metadata_dir()):
        print(rel)


def cmd_to_music(args):
    repo = metadata_dir()
    music = Path(os.environ.get("MUSIC_DIR", "")).expanduser()
    if not music.is_dir():
        die("$MUSIC_DIR not set or not a dir")
    rels = args.rels or read_rels(sys.stdin)
    if not rels:
        die("no files given (pipe `lyrics-push.py list` through fzf)")

    plan, overwrite, missing = [], [], []
    for rel in rels:
        src = repo / "lyrics" / rel
        dst = music / rel
        if not src.is_file():
            missing.append(rel); continue
        (overwrite if dst.exists() else plan).append((src, dst, rel))

    for rel in missing:
        print(f"skip (no source): {rel}", file=sys.stderr)
    for _s, _d, rel in plan:
        print(f"new      {rel}")
    for _s, _d, rel in overwrite:
        print(f"OVERWRITE {rel}")

    todo = plan + overwrite
    if not todo:
        return
    if overwrite and not args.yes:
        if input(f"\noverwrite {len(overwrite)} existing sidecar(s)? [y/N] ").strip().lower() != "y":
            print("nothing copied"); return

    n = 0
    for src, dst, rel in todo:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    print(f"\ncopied {n} file(s) into {music}")


def cmd_to_drive(args):
    repo = metadata_dir()
    remote = os.environ.get("RCLONE_MUSIC_REMOTE_PATH")
    if not remote:
        die("$RCLONE_MUSIC_REMOTE_PATH not set")
    if not shutil.which("rclone"):
        die("rclone not on PATH")
    rels = args.rels or read_rels(sys.stdin)
    if not rels:
        die("no files given (pipe `lyrics-push.py list` through fzf)")

    ok = fail = 0
    for rel in rels:
        src = repo / "lyrics" / rel
        if not src.is_file():
            print(f"skip (no source): {rel}", file=sys.stderr); continue
        dest = f"{remote.rstrip('/')}/{rel}"
        cmd = ["rclone", "copyto", str(src), dest]
        if args.dry_run:
            print("  " + " ".join(cmd))
            ok += 1
            continue
        rc = subprocess.run(cmd).returncode
        if rc == 0:
            print(f"uploaded {rel}")
            ok += 1
        else:
            print(f"FAILED   {rel}  (rclone exit {rc})", file=sys.stderr)
            fail += 1
    print(f"\n{'would upload' if args.dry_run else 'uploaded'} {ok}, failed {fail}")
    return 1 if fail else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("to-music")
    p.add_argument("rels", nargs="*", help="lyrics-relative paths (else read stdin)")
    p.add_argument("--yes", action="store_true", help="don't prompt before overwriting")
    p = sub.add_parser("to-drive")
    p.add_argument("rels", nargs="*", help="lyrics-relative paths (else read stdin)")
    p.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.exit({"list": cmd_list, "to-music": cmd_to_music, "to-drive": cmd_to_drive}[args.cmd](args) or 0)


if __name__ == "__main__":
    main()
