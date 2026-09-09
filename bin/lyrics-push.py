#!/usr/bin/env python3
"""lyrics-push.py - push lyrics files out to the music collection or Drive.

Uncommitted (freshly fetched / hand-edited):
    list        every .txt/.lrc/.elrc under $MUSIC_METADATA_DIR/lyrics that git
                has NOT committed yet (staged or not; excludes lrc-sync *.old
                and deletions). One lyrics-relative path per line, for `fzf -m`.
    to-music    copy the picked files next to the mp3 at $MUSIC_DIR/<rel> so
                mpd / mpdtui pick them up (overwrite existing after one confirm).
    to-drive    rclone copyto the picked files to $RCLONE_MUSIC_REMOTE_PATH/<rel>.

By commit:
    commits [-n N]    the last N commits touching lyrics/, annotated with
                      whether they've already been pushed to Drive. For `fzf -m`.
    push-commits      rclone the lyrics/ files from the picked commits to Drive
                      and record each commit in the push ledger.

Nothing is ever moved, staged, committed or deleted - the music-metadata
working tree is untouched.

    lyrics-push.py list | fzf -m | lyrics-push.py to-music
    lyrics-push.py commits | fzf -m | lyrics-push.py push-commits
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LYRICS_EXTS = (".txt", ".lrc", ".elrc")

# The push ledger lives IN music-metadata (travels with the data, shared across
# machines): one line per commit pushed to Drive -
#   <full-hash>\t<iso8601>\t<n-files>\t<subject>
# It is a plain tracked file - the tool only appends; you commit it yourself.
LEDGER_REL = "lyrics-reports/drive-push.log"


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


def read_tokens(stream):
    """First whitespace-delimited token of each non-empty line."""
    toks = []
    for line in stream:
        line = line.strip()
        if line:
            toks.append(line.split()[0])
    return toks


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


# --------------------------------------------------------------- push ledger
def ledger_path(repo):
    return repo / LEDGER_REL


def read_ledger(repo):
    """full-hash -> (iso timestamp, n files) for the most recent push of each."""
    p = ledger_path(repo)
    seen = {}
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and len(parts[0]) == 40:
                seen[parts[0]] = (parts[1], parts[2])
    return seen


def append_ledger(repo, full_hash, n, subject):
    p = ledger_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with p.open("a", encoding="utf-8") as fh:
        fh.write(f"{full_hash}\t{stamp}\t{n}\t{subject}\n")


# --------------------------------------------------------------- by commit
def commits_touching_lyrics(repo, n):
    out = _git(repo, "log", f"-n{n}", "--format=%H%x1f%h%x1f%s", "--", "lyrics")
    rows = []
    for line in out.splitlines():
        full, short, subject = line.split("\x1f", 2)
        rows.append((full, short, subject))
    return rows


def lyrics_files_in_commit(repo, ref):
    out = _git(repo, "show", "--pretty=format:", "--name-status", "--no-renames", ref, "--", "lyrics")
    rels = set()
    for line in out.splitlines():
        if not line or "\t" not in line:
            continue
        status, _, path = line.partition("\t")
        if status[:1] not in ("A", "M"):        # skip D; renames show as A+D here
            continue
        if not path.startswith("lyrics/") or not path.endswith(LYRICS_EXTS):
            continue
        if ".old" in Path(path).name:
            continue
        rels.add(path[len("lyrics/"):])
    return sorted(rels)


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


def cmd_commits(args):
    repo = metadata_dir()
    pushed = read_ledger(repo)
    for full, short, subject in commits_touching_lyrics(repo, args.n):
        k = len(lyrics_files_in_commit(repo, full))
        flag = f"pushed {pushed[full][0][:10]}" if full in pushed else "new"
        print(f"{full}  {short}  [{flag:>17}]  {subject}  ({k} lyrics)")


def cmd_push_commits(args):
    repo = metadata_dir()
    remote = os.environ.get("RCLONE_MUSIC_REMOTE_PATH")
    if not remote:
        die("$RCLONE_MUSIC_REMOTE_PATH not set")
    if not shutil.which("rclone"):
        die("rclone not on PATH")
    refs = args.refs or read_tokens(sys.stdin)
    if not refs:
        die("no commits given (pipe `lyrics-push.py commits` through fzf)")

    # resolve to full hashes + gather files, keeping the newest file per rel
    plan = {}          # rel -> None (just a set, ordered later)
    per_commit = []     # (full, subject, [rels])
    for ref in refs:
        try:
            full = _git(repo, "rev-parse", ref).strip()
            subject = _git(repo, "log", "-1", "--format=%s", full).strip()
        except subprocess.CalledProcessError:
            die(f"not a commit: {ref}")
        rels = lyrics_files_in_commit(repo, full)
        per_commit.append((full, subject, rels))
        for r in rels:
            plan[r] = None

    for full, subject, rels in per_commit:
        print(f"{full[:10]}  {subject}  ({len(rels)} lyrics)")
    print(f"\n{len(plan)} unique file(s) -> {remote}\n")

    ok = fail = skipped = 0
    for rel in sorted(plan):
        src = repo / "lyrics" / rel
        if not src.is_file():
            print(f"skip (gone from working tree): {rel}", file=sys.stderr)
            skipped += 1
            continue
        cmd = ["rclone", "copyto", str(src), f"{remote.rstrip('/')}/{rel}"]
        if args.dry_run:
            print("  " + " ".join(cmd)); ok += 1; continue
        if subprocess.run(cmd).returncode == 0:
            print(f"uploaded {rel}"); ok += 1
        else:
            print(f"FAILED   {rel}", file=sys.stderr); fail += 1

    print(f"\n{'would upload' if args.dry_run else 'uploaded'} {ok}, failed {fail}, skipped {skipped}")
    if args.dry_run or fail:
        return 1 if fail else 0
    for full, subject, rels in per_commit:
        append_ledger(repo, full, len(rels), subject)
    print(f"\nrecorded {len(per_commit)} commit(s) in {LEDGER_REL} "
          f"(now dirty in music-metadata - commit it when ready)")
    return 0


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
    p = sub.add_parser("commits")
    p.add_argument("-n", type=int, default=5, help="how many recent lyrics commits to list (default 5)")
    p = sub.add_parser("push-commits")
    p.add_argument("refs", nargs="*", help="commit refs (else read leading token per stdin line)")
    p.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.exit({"list": cmd_list, "to-music": cmd_to_music, "to-drive": cmd_to_drive,
              "commits": cmd_commits, "push-commits": cmd_push_commits}[args.cmd](args) or 0)


if __name__ == "__main__":
    main()
