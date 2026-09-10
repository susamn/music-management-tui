#!/usr/bin/env python3
"""mpdtui.py - read, diff and edit the mpdtui SQLite DB.

The DB (~/.config/mpdtui/mpdtui.db by default) holds one row per track:
play_count, rating (0-5). Marks (-> mark_reason via track_marks) and tags
(-> tags via track_tags) are both many-to-many. real_path is repo-relative
and lives on disk at $MUSIC_DIR/<real_path>.

Tracks the mpdtui schema as it stands today; mpdtui is under active
development, so a `no such column/table` error here means the DB shape moved
- open mpdtui once (it migrates on open) or update this script.

Read:
    mpdtui.py summary
    mpdtui.py list [--min-rating N] [--mark ID] [--tag NAME] [--played]
    mpdtui.py marks                      # the mark_reason vocabulary
    mpdtui.py tags                       # tags + how many tracks each

Diff:
    mpdtui.py orphans                    # rows whose file is gone
    mpdtui.py diff-library               # disk vs DB (missing / orphan / drift)
    mpdtui.py diff-stats [--csv F]       # rating / play_count vs play_stats.csv

Write (each takes a DB backup first; needs --apply, else it's a dry run):
    <id-list> | mpdtui.py mark --add "Syncing needed" --apply
    <id-list> | mpdtui.py mark --remove 6 --apply
    <id-list> | mpdtui.py mark --clear --apply        # drop all marks
    <id-list> | mpdtui.py tag --add bengali --apply
    <id-list> | mpdtui.py tag --remove hindi --apply
    mpdtui.py tag --rename english=en --apply
    mpdtui.py import-ratings [--csv F] [--apply]
    mpdtui.py prune [--apply]

An <id-list> on stdin is any text with a leading integer track id per line -
pipe `mpdtui.py list` through `fzf -m` straight into these.

--db / --music / --csv default to $MPDTUI_DB / $MUSIC_DIR / $PLAY_STATS_CSV.
Stdlib only.
"""
import argparse
import csv
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

AUDIO_EXTS = (".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".wma", ".aac")
_key_ext = re.compile(r"\.(txt|lrc|mp3|m4a|flac|ogg|opus|wav|wma|aac)$", re.I)
_key_mid = re.compile(r"-\[mid-[^\]]*\]$")
_key_num = re.compile(r"^\d+(-\d+)?-")


def die(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def normalize(s):
    # match mpdtui internal/metadata.normalizeSegment: per segment, keep only
    # Unicode letters/digits, lowercased; '/' between segments is preserved.
    return "".join(c.lower() for c in s if c == "/" or c.isalnum())


def repo_join_key(rel):
    rel = _key_ext.sub("", rel)
    d, _, base = rel.rpartition("/")
    base = _key_mid.sub("", base)
    base = _key_num.sub("", base)
    return f"{d}/{base}"


def connect(path):
    p = Path(path).expanduser()
    if not p.is_file():
        die(f"mpdtui DB not found: {p}")
    con = sqlite3.connect(f"file:{p}?mode=rw", uri=True)
    con.row_factory = sqlite3.Row
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    for t in ("tracks", "mark_reason", "tags", "track_marks", "track_tags"):
        if t not in have:
            die(f"table {t!r} missing from {p} - open mpdtui once so it migrates, "
                "or this script is behind the schema")
    return con


LINK = {  # subcommand -> (join table, join fk column, catalog table, catalog name column)
    "mark": ("track_marks", "mark_id", "mark_reason", "reason"),
    "tag": ("track_tags", "tag_id", "tags", "tagname"),
}


_backed_up = {}


def backup(db_path, apply):
    if not apply:
        return
    p = Path(db_path).expanduser()
    if str(p) in _backed_up:
        return
    dest = p.with_name(p.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(p, dest)
    _backed_up[str(p)] = dest
    print(f"backup: {dest}", file=sys.stderr)


def read_ids(stream):
    ids = []
    for line in stream:
        m = re.match(r"\s*(\d+)", line)
        if m:
            ids.append(int(m.group(1)))
    return ids


# --------------------------------------------------------------------- reads
def cmd_summary(con, args):
    r = con.execute("""
        SELECT COUNT(*) n,
               SUM(rating > 0) rated,
               SUM(play_count > 0) played,
               (SELECT COUNT(DISTINCT track_id) FROM track_marks) marked
        FROM tracks""").fetchone()
    print(f"tracks   {r['n']}")
    print(f"  rated  {r['rated']}")
    print(f"  played {r['played']}")
    print(f"  marked {r['marked']}")
    print("\nby rating:")
    for row in con.execute("SELECT rating, COUNT(*) c FROM tracks GROUP BY rating ORDER BY rating"):
        print(f"  {row['rating']}  {row['c']}")
    print("\nby mark:")
    for row in con.execute("""
            SELECT mr.reason, COUNT(tm.track_id) c FROM mark_reason mr
            LEFT JOIN track_marks tm ON tm.mark_id = mr.id
            GROUP BY mr.id ORDER BY c DESC"""):
        print(f"  {row['c']:5d}  {row['reason']}")
    print("\nby tag:")
    for row in con.execute("""
            SELECT tg.tagname, COUNT(tt.track_id) c FROM tags tg
            LEFT JOIN track_tags tt ON tt.tag_id = tg.id
            GROUP BY tg.id ORDER BY c DESC"""):
        print(f"  {row['c']:5d}  {row['tagname']}")


def _list_rows(con, args):
    q = ["""SELECT t.id, t.rating, t.play_count, t.real_path,
               (SELECT GROUP_CONCAT(mr.reason, ',') FROM track_marks tm
                JOIN mark_reason mr ON mr.id = tm.mark_id
                WHERE tm.track_id = t.id) marks
            FROM tracks t"""]
    where, params = [], []
    if args.min_rating is not None:
        where.append("t.rating >= ?"); params.append(args.min_rating)
    if args.mark is not None:
        where.append("EXISTS (SELECT 1 FROM track_marks tm WHERE tm.track_id = t.id "
                     "AND tm.mark_id = ?)"); params.append(args.mark)
    if args.tag:
        where.append("EXISTS (SELECT 1 FROM track_tags tt JOIN tags tg ON tg.id = tt.tag_id "
                     "WHERE tt.track_id = t.id AND tg.tagname = ?)"); params.append(args.tag)
    if args.played:
        where.append("t.play_count > 0")
    if where:
        q.append("WHERE " + " AND ".join(where))
    q.append("ORDER BY t.real_path")
    return con.execute(" ".join(q), params).fetchall()


def cmd_list(con, args):
    for r in _list_rows(con, args):
        print(f"{r['id']}\t{r['rating']}★\t{r['play_count']}p\t{r['marks'] or '-'}\t{r['real_path']}")


def cmd_marks(con, args):
    for r in con.execute("SELECT id, reason FROM mark_reason ORDER BY id"):
        print(f"{r['id']}\t{r['reason']}")


def cmd_tags(con, args):
    for r in con.execute("""
            SELECT tg.id, tg.tagname, COUNT(tt.track_id) c FROM tags tg
            LEFT JOIN track_tags tt ON tt.tag_id = tg.id
            GROUP BY tg.id ORDER BY tg.tagname"""):
        print(f"{r['id']}\t{r['tagname']}\t{r['c']} tracks")


# --------------------------------------------------------------------- diffs
def _disk_rel_paths(music):
    root = Path(music).expanduser()
    if not root.is_dir():
        die(f"music dir not found: {root}")
    for p in root.rglob("*"):
        if p.suffix.lower() in AUDIO_EXTS:
            yield str(p.relative_to(root))


def cmd_orphans(con, args):
    root = Path(args.music).expanduser()
    n = 0
    for r in con.execute("SELECT real_path FROM tracks ORDER BY real_path"):
        if not (root / r["real_path"]).exists():
            print(r["real_path"]); n += 1
    print(f"\n{n} orphan row(s)", file=sys.stderr)


def cmd_diff_library(con, args):
    db = {r["normalized_path"]: r["real_path"]
          for r in con.execute("SELECT normalized_path, real_path FROM tracks")}
    disk = {normalize(rel): rel for rel in _disk_rel_paths(args.music)}

    not_yet = sorted(disk[k] for k in disk.keys() - db.keys())
    orphan = sorted(db[k] for k in db.keys() - disk.keys())
    drift = sorted((db[k], disk[k]) for k in db.keys() & disk.keys() if db[k] != disk[k])

    # mpdtui only rows a track once you touch it, so "on disk, not in DB" is
    # mostly just un-rated tracks -- a count, not a problem. The real integrity
    # checks are orphan rows and path drift.
    print(f"in DB, not on disk (orphan rows) : {len(orphan)}")
    for o in orphan:
        print(f"  - {o}")
    print(f"\npath drift (normalized match, real_path differs) : {len(drift)}")
    for a, b in drift:
        print(f"  ~ db: {a}\n    fs: {b}")
    print(f"\non disk, no DB row yet : {len(not_yet)}"
          + ("   (--full to list)" if not_yet and not args.full else ""))
    if args.full:
        for m in not_yet:
            print(f"  + {m}")


def cmd_diff_stats(con, args):
    csv_path = Path(args.csv).expanduser()
    if not csv_path.is_file():
        die(f"play_stats.csv not found: {csv_path}")
    stats = {}
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("join_key"):
                stats[row["join_key"]] = row

    rating_diffs, play_diffs, unmatched = [], [], 0
    for r in con.execute("SELECT real_path, rating, play_count FROM tracks"):
        s = stats.get(repo_join_key(r["real_path"]))
        if not s:
            unmatched += 1
            continue
        if s.get("rating_kind") == "user":
            apple_stars = round(int(s["rating"] or 0) / 20)
            if apple_stars != r["rating"]:
                rating_diffs.append((r["real_path"], r["rating"], apple_stars))
        try:
            apple_pc = int(s.get("play_count") or 0)
        except ValueError:
            apple_pc = 0
        if apple_pc != r["play_count"]:
            play_diffs.append((r["real_path"], r["play_count"], apple_pc))

    print(f"rating differs (mpdtui vs Apple user-rating) : {len(rating_diffs)}")
    for path, m, a in rating_diffs:
        print(f"  {m}★ -> {a}★   {path}")
    print(f"\nplay_count differs : {len(play_diffs)}")
    for path, m, a in play_diffs[:80]:
        print(f"  {m} -> {a}   {path}")
    if len(play_diffs) > 80:
        print(f"  ... and {len(play_diffs) - 80} more")
    print(f"\n{unmatched} DB row(s) had no play_stats join", file=sys.stderr)


# --------------------------------------------------------------------- writes
def _confirm(msg, args):
    if args.yes:
        return True
    # stdin is usually the piped id-list here, so prompt on the terminal.
    try:
        if sys.stdin.isatty():
            resp = input(f"{msg} [y/N] ")
        else:
            with open("/dev/tty", "r") as tty:
                sys.stderr.write(f"{msg} [y/N] ")
                sys.stderr.flush()
                resp = tty.readline()
    except (OSError, EOFError):
        return False
    return resp.strip().lower() == "y"


def _catalog_id(con, kind, token, create=False):
    """Resolve a mark/tag by numeric id or by name. create=True inserts a name."""
    link, fk, catalog, namecol = LINK[kind]
    if re.fullmatch(r"\d+", str(token)):
        row = con.execute(f"SELECT id, {namecol} n FROM {catalog} WHERE id = ?", (int(token),)).fetchone()
        return (row["id"], row["n"]) if row else (None, None)
    row = con.execute(f"SELECT id FROM {catalog} WHERE {namecol} = ?", (token,)).fetchone()
    if row:
        return row["id"], token
    if create:
        cur = con.execute(f"INSERT INTO {catalog}({namecol}) VALUES (?)", (token,))
        return cur.lastrowid, token
    return None, None


def _edit_link(con, kind, args):
    link, fk, catalog, namecol = LINK[kind]

    if getattr(args, "rename", None):
        old, _, new = args.rename.partition("=")
        if not old or not new:
            die("--rename wants OLD=NEW")
        cid, _ = _catalog_id(con, kind, old)
        if cid is None:
            die(f"no {kind} {old!r}")
        print(f"rename {kind} {old!r} -> {new!r}")
        if not args.apply:
            print("dry run - pass --apply"); return
        if not _confirm("apply?", args):
            return
        backup(args.db, True)
        con.execute(f"UPDATE {catalog} SET {namecol} = ? WHERE id = ?", (new, cid))
        con.commit()
        print("done"); return

    ids = read_ids(sys.stdin)
    if not ids:
        die("no track ids on stdin (pipe `mpdtui.py list` through fzf)")

    if getattr(args, "clear", False):
        print(f"remove ALL {kind}s from {len(ids)} track(s)")
        if not args.apply:
            print("dry run - pass --apply"); return
        if not _confirm("apply?", args):
            return
        backup(args.db, True)
        con.executemany(f"DELETE FROM {link} WHERE track_id = ?", [(i,) for i in ids])
        con.commit()
        print(f"{con.total_changes} link(s) removed"); return

    token = args.add or args.remove
    if not token:
        die(f"give --add X, --remove X, --clear or --rename OLD=NEW")
    adding = bool(args.add)
    cid, name = _catalog_id(con, kind, token, create=adding)
    if cid is None:
        die(f"no {kind} {token!r} (see: mpdtui.py {kind}s)")
    print(f"{'add' if adding else 'remove'} {kind} {name!r} {'to' if adding else 'from'} {len(ids)} track(s)")
    if not args.apply:
        print("dry run - pass --apply to write"); return
    if not _confirm("apply?", args):
        return
    backup(args.db, True)
    if adding:
        con.executemany(f"INSERT OR IGNORE INTO {link}(track_id, {fk}) VALUES (?, ?)",
                        [(i, cid) for i in ids])
    else:
        con.executemany(f"DELETE FROM {link} WHERE track_id = ? AND {fk} = ?",
                        [(i, cid) for i in ids])
    con.commit()
    print(f"{con.total_changes} change(s)")


def cmd_mark(con, args):
    _edit_link(con, "mark", args)


def cmd_tag(con, args):
    _edit_link(con, "tag", args)


def cmd_import_ratings(con, args):
    csv_path = Path(args.csv).expanduser()
    if not csv_path.is_file():
        die(f"play_stats.csv not found: {csv_path}")
    apple = {}
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("join_key") and row.get("rating_kind") == "user":
                apple[row["join_key"]] = round(int(row["rating"] or 0) / 20)

    updates = []
    for r in con.execute("SELECT id, real_path, rating FROM tracks"):
        want = apple.get(repo_join_key(r["real_path"]))
        if want is not None and want != r["rating"]:
            updates.append((want, r["id"], r["real_path"], r["rating"]))

    print(f"{len(updates)} rating(s) would change from Apple user-ratings:")
    for want, _id, path, cur in updates[:60]:
        print(f"  {cur}★ -> {want}★   {path}")
    if len(updates) > 60:
        print(f"  ... and {len(updates) - 60} more")
    if not args.apply:
        print("\ndry run - pass --apply to write"); return
    if not _confirm(f"\nwrite {len(updates)} rating(s)?", args):
        return
    backup(args.db, True)
    con.executemany("UPDATE tracks SET rating = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    [(u[0], u[1]) for u in updates])
    con.commit()
    print(f"updated {con.total_changes} row(s)")


def cmd_prune(con, args):
    root = Path(args.music).expanduser()
    gone = [r["id"] for r in con.execute("SELECT id, real_path FROM tracks")
            if not (root / r["real_path"]).exists()]
    print(f"{len(gone)} orphan row(s) (file missing under {root})")
    if not gone or not args.apply:
        if gone:
            print("dry run - pass --apply to delete")
        return
    if not _confirm(f"delete {len(gone)} row(s)?", args):
        return
    backup(args.db, True)
    con.executemany("DELETE FROM tracks WHERE id = ?", [(i,) for i in gone])
    con.commit()
    print(f"deleted {con.total_changes} row(s)")


# --------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("MPDTUI_DB", "~/.config/mpdtui/mpdtui.db"))
    ap.add_argument("--music", default=os.environ.get("MUSIC_DIR", ""))
    ap.add_argument("--csv", default=os.environ.get("PLAY_STATS_CSV", ""))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("summary")
    p = sub.add_parser("list")
    p.add_argument("--min-rating", type=int)
    p.add_argument("--mark", type=int)
    p.add_argument("--tag")
    p.add_argument("--played", action="store_true")
    sub.add_parser("marks")
    sub.add_parser("tags")
    sub.add_parser("orphans")
    p = sub.add_parser("diff-library")
    p.add_argument("--full", action="store_true", help="also list every un-rowed file")
    sub.add_parser("diff-stats")

    for name in ("mark", "tag"):
        p = sub.add_parser(name)
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--add", metavar="ID_OR_NAME",
                       help=f"add this {name} to the piped tracks (creates it if a new name)")
        g.add_argument("--remove", metavar="ID_OR_NAME",
                       help=f"remove this {name} from the piped tracks")
        g.add_argument("--clear", action="store_true",
                       help=f"remove ALL {name}s from the piped tracks")
        g.add_argument("--rename", metavar="OLD=NEW", help=f"rename a {name} in the catalog")
        p.add_argument("--apply", action="store_true")
        p.add_argument("--yes", action="store_true")

    p = sub.add_parser("import-ratings")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--yes", action="store_true")

    p = sub.add_parser("prune")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--yes", action="store_true")

    args = ap.parse_args()
    if args.cmd in ("orphans", "diff-library", "prune") and not args.music:
        die("need --music or $MUSIC_DIR")
    if args.cmd in ("diff-stats", "import-ratings") and not args.csv:
        die("need --csv or $PLAY_STATS_CSV")

    con = connect(args.db)
    try:
        {
            "summary": cmd_summary, "list": cmd_list, "marks": cmd_marks, "tags": cmd_tags,
            "orphans": cmd_orphans, "diff-library": cmd_diff_library, "diff-stats": cmd_diff_stats,
            "mark": cmd_mark, "tag": cmd_tag, "import-ratings": cmd_import_ratings, "prune": cmd_prune,
        }[args.cmd](con, args)
    finally:
        con.close()


if __name__ == "__main__":
    main()
