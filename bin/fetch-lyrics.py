#!/usr/bin/env python3
"""fetch-lyrics.py - pull lyrics from LRCLIB for a whole music tree and write
them into the lyrics collection at the mirrored path.

Consolidates the old ~/workspace/scripts/fetch_lyrics.py / fetch_lrc.py /
fetch_elrc.py.  Pick what to fetch with --kind:

  plain   plainLyrics   -> <out>/<rel>.txt
  lrc     syncedLyrics  -> <out>/<rel>.lrc
  elrc    syncedLyrics, but only when it carries <mm:ss.xx> word tags (A2)
                        -> <out>/<rel>.elrc

For a track at  <music>/artist/album/NN-title.mp3  the lyrics land at
  <out>/artist/album/NN-title.<ext>      (out defaults to $MUSIC_METADATA_DIR/lyrics)

Existing files are skipped unless --force.  Metadata (title/artist/album/
duration) is read with ffprobe.  Stdlib only; needs ffprobe on PATH.

    fetch-lyrics.py --kind plain --music ~/Music/susamn-music-collection
    fetch-lyrics.py --kind lrc   --music ~/Music/… --only adele/25
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

AUDIO_EXTS = (".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav")
UA = "music-tui/1.0 (+https://github.com/susamn)"
A2_RE = re.compile(r"<\d{2}:\d{2}\.\d{2,3}>")

KINDS = {
    "plain": {"field": "plainLyrics", "ext": ".txt"},
    "lrc":   {"field": "syncedLyrics", "ext": ".lrc"},
    "elrc":  {"field": "syncedLyrics", "ext": ".elrc"},  # + A2 check
}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def ffprobe_tags(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            fmt = json.loads(r.stdout).get("format", {})
            return fmt.get("tags", {}), fmt.get("duration")
    except Exception as e:
        log(f"ffprobe error {path}: {e}")
    return {}, None


def _get(tags, *names):
    for n in names:
        for k in (n, n.upper(), n.lower(), n.capitalize()):
            if tags.get(k):
                return tags[k]
    return None


def _api(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=8) as resp:
        if resp.status == 200:
            return json.loads(resp.read().decode("utf-8"))
    return None


def fetch_field(field, title, artist, album, duration):
    if not title:
        return None
    params = {"track_name": title}
    if artist:
        params["artist_name"] = artist
    if album:
        params["album_name"] = album
    if duration:
        try:
            params["duration"] = str(int(float(duration)))
        except ValueError:
            pass
    try:
        data = _api("https://lrclib.net/api/get?" + urllib.parse.urlencode(params))
        if data and data.get(field):
            return data[field]
    except Exception:
        pass
    # fall back to search
    q = f"{title} {artist}".strip()
    try:
        data = _api("https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": q}))
        if isinstance(data, list):
            for item in data:
                if item.get(field):
                    return item[field]
    except Exception:
        pass
    return None


def process(path, music_root, out_root, kind, force, delay):
    spec = KINDS[kind]
    rel = path.relative_to(music_root)
    dest = (out_root / rel).with_suffix(spec["ext"])
    if dest.exists() and not force:
        return ("skip", rel)

    tags, duration = ffprobe_tags(path)
    title = _get(tags, "title")
    artist = _get(tags, "artist", "album_artist", "artists")
    album = _get(tags, "album")
    if not title:
        return ("notag", rel)

    body = fetch_field(spec["field"], title, artist, album, duration)
    time.sleep(delay)
    if not body:
        return ("miss", rel)
    if kind == "elrc" and not A2_RE.search(body):
        return ("not-a2", rel)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    return ("ok", rel)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kind", required=True, choices=KINDS)
    ap.add_argument("--music", default=os.environ.get("MUSIC_DIR"),
                    help="music library root to walk (default: $MUSIC_DIR)")
    ap.add_argument("--out", default=None,
                    help="lyrics output root (default: $MUSIC_METADATA_DIR/lyrics)")
    ap.add_argument("--only", default="",
                    help="restrict to this sub-path under --music")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between API calls per worker")
    ap.add_argument("--force", action="store_true", help="overwrite existing lyrics files")
    args = ap.parse_args()

    if not args.music:
        ap.error("--music not given and $MUSIC_DIR not set")
    music_root = Path(args.music).expanduser().resolve()
    if not music_root.is_dir():
        ap.error(f"music dir not found: {music_root}")

    if args.out:
        out_root = Path(args.out).expanduser().resolve()
    elif os.environ.get("MUSIC_METADATA_DIR"):
        out_root = Path(os.environ["MUSIC_METADATA_DIR"]).expanduser().resolve() / "lyrics"
    else:
        ap.error("--out not given and $MUSIC_METADATA_DIR not set")

    walk_root = music_root / args.only if args.only else music_root
    files = [p for p in walk_root.rglob("*") if p.suffix.lower() in AUDIO_EXTS]
    log(f"{len(files)} audio files under {walk_root}  ->  {args.kind}  ->  {out_root}")

    counts = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(process, p, music_root, out_root, args.kind, args.force, args.delay)
                for p in files]
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            try:
                status, rel = fut.result()
            except Exception as e:
                status, rel = "error", e
            counts[status] = counts.get(status, 0) + 1
            done += 1
            if status == "ok":
                log(f"[{done}/{len(files)}] ok    {rel}")
            elif status in ("miss", "not-a2", "error"):
                log(f"[{done}/{len(files)}] {status:5s} {rel}")

    log("")
    for k in ("ok", "skip", "miss", "not-a2", "notag", "error"):
        if k in counts:
            log(f"  {k:7s} {counts[k]}")
    return 0 if counts.get("error", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
