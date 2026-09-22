#!/usr/bin/env python3
"""merge-m3u.py - git merge driver for playlists/*.m3u: union, deduplicated.

playlist-sync.py rewrites a playlist's whole .m3u from Apple Music.app's
current state on every run. Two machines each running it independently will
diverge - a normal 3-way text merge conflicts on nearly every line, since
almost nothing aligns between two full rewrites. This driver sidesteps that
entirely: keep every track from "ours" in its existing order, then append
whatever "theirs" has that "ours" doesn't. Both sides' additions are
guaranteed to survive, deduplicated, with no conflict markers ever.

Tradeoff, by design: a track *removed* on only one side can resurface,
since union-by-addition can't tell "never added" apart from "removed" -
only additions are guaranteed-safe. In practice, playlists here are
overwhelmingly append-heavy (Apple Music growing your library), so this
tends not to matter; if it ever does, resolve that one playlist by hand and
re-run playlist-sync.py to regenerate it cleanly.

git invokes this as: merge-m3u.py <base> <ours> <theirs>, and expects the
merged result written back to <ours>. Always exits 0 - never signals an
unresolved conflict, which is the entire point of a union driver.

Registered via .gitattributes (playlists/*.m3u merge=m3u-union) in
music-metadata, and wired up automatically in this repo's local git config
by music-tui.sh on every launch - see docs/playlist-sync.md.
"""
import sys


def load(path):
    with open(path, encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f]


def tracks(lines):
    return [line for line in lines if line.strip() and not line.startswith("#")]


def main():
    if len(sys.argv) < 4:
        sys.exit("usage: merge-m3u.py <base> <ours> <theirs>")
    _base, ours_path, theirs_path = sys.argv[1], sys.argv[2], sys.argv[3]

    ours = load(ours_path)
    theirs = load(theirs_path)

    seen = set()
    merged = ["#EXTM3U"]
    for line in tracks(ours) + tracks(theirs):
        if line not in seen:
            seen.add(line)
            merged.append(line)

    with open(ours_path, "w", encoding="utf-8") as f:
        f.write("\n".join(merged) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
