#!/usr/bin/env python3
"""wiki-fetch.py - build per-track wiki.json (+ images) from online sources.

Writes into the wiki collection at the mirrored path, one directory per track:

    <music>/artist/album/NN-title.mp3
 -> <wiki>/artist/album/NN-title/wiki.json
                                 cover.jpg, artist.jpg, scene-NN.jpg, ...

Every field falls back down a chain of sources and takes the first that has
anything: nothing is merged, so a field always has exactly one provenance and
the file cannot end up quoting two services against each other.

    story              wikipedia -> lastfm -> genius -> discogs
    behind_the_scenes  genius -> discogs -> wikipedia
    bootlegs           musicbrainz
    images             coverartarchive -> lastfm -> genius

Sources without a configured API key are skipped, silently and by design: the
chain simply moves on, so a machine with no keys still gets MusicBrainz,
Wikipedia and Cover Art Archive.

Sweeps in batches (--limit, default $WIKI_BATCH or 100) so a run is a known
quantity of network traffic. Tracks that already have a wiki.json are skipped,
so the next run continues where this one stopped. Tracks no source knew
anything about are recorded in the reports dir and skipped too, until
--retry-missing.

Stdlib only; needs ffprobe on PATH.

    wiki-fetch.py --dry-run                 # what the next batch would do
    wiki-fetch.py                           # fetch one batch
    wiki-fetch.py --only sade               # restrict to a subtree
    wiki-fetch.py --limit 5 --force         # refetch, small batch
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
AUDIO_EXTS = (".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav")
IMAGE_EXTS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

# Per-host minimum seconds between requests. MusicBrainz documents one request
# per second and enforces it with 503s; the rest are courtesy values. Keyed by
# hostname so a chain hitting four different services is not serialised behind
# the slowest one's limit.
RATE_LIMITS = {"musicbrainz.org": 1.1, "coverartarchive.org": 1.1, "api.discogs.com": 1.1}
DEFAULT_RATE = 0.34

_last_request = {}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------- config

def load_config():
    """Config from the environment, falling back to ~/.config/music-tui/config.

    music-tui.sh exports everything before running us, but a script that only
    works when launched from the menu is a script you cannot debug, so the file
    is parsed directly when the variables are not already set.
    """
    conf = {}
    path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "music-tui" / "config"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip()
            # The file is sourced by bash, so a value containing shell
            # metacharacters -- the User-Agent's parentheses, above all -- has
            # to be quoted there. Those quotes are shell syntax, not part of
            # the value.
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            conf[k.strip()] = v
    conf.update({k: v for k, v in os.environ.items() if k in CONFIG_KEYS and v})
    return conf


CONFIG_KEYS = {
    "MUSIC_DIR", "MUSIC_METADATA_DIR", "WIKI_DIR", "WIKI_REPORTS_DIR", "WIKI_BATCH",
    "WIKI_USER_AGENT", "WIKI_LANG",
    "MUSICBRAINZ_API", "COVERART_API", "WIKIPEDIA_API", "LASTFM_API", "GENIUS_API", "DISCOGS_API",
    "LASTFM_API_KEY", "GENIUS_TOKEN", "DISCOGS_TOKEN",
}

DEFAULTS = {
    "WIKI_BATCH": "100",
    "WIKI_LANG": "en",
    "WIKI_USER_AGENT": "music-tui/1.0 ( https://github.com/susamn )",
    "MUSICBRAINZ_API": "https://musicbrainz.org/ws/2",
    "COVERART_API": "https://coverartarchive.org",
    "WIKIPEDIA_API": "https://{lang}.wikipedia.org",
    "LASTFM_API": "https://ws.audioscrobbler.com/2.0/",
    "GENIUS_API": "https://api.genius.com",
    "DISCOGS_API": "https://api.discogs.com",
}


def expand(p):
    return Path(os.path.expanduser(str(p)))


def cfg(conf, key):
    return conf.get(key) or DEFAULTS.get(key, "")


# ------------------------------------------------------------------- fetching

def _throttle(url):
    host = urllib.parse.urlparse(url).hostname or ""
    gap = RATE_LIMITS.get(host, DEFAULT_RATE)
    now = time.monotonic()
    wait = _last_request.get(host, 0) + gap - now
    if wait > 0:
        time.sleep(wait)
    _last_request[host] = time.monotonic()


def http_get(url, conf, headers=None, timeout=20, binary=False, retries=3):
    """One GET, throttled per host. Returns bytes/str, or None on any failure.

    Every caller treats None as "this source has nothing", so a 404, a timeout
    and a service being down all land in the same place: the next source in the
    chain. Failures are logged, never raised -- one dead service must not end a
    sweep that still has ninety tracks to go.

    503 and 429 are retried with a widening pause rather than given up on.
    MusicBrainz answers a sustained sweep with 503 even at its documented one
    request per second, and treating that as "no data" would quietly cost the
    bootlegs and cover art for a good share of a hundred-track run.
    """
    req = urllib.request.Request(url, headers={"User-Agent": cfg(conf, "WIKI_USER_AGENT"), **(headers or {})})
    for attempt in range(retries):
        _throttle(url)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                return data if binary else data.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (503, 429) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            if e.code not in (404, 400):
                log(f"    ! {e.code} {url.split('?')[0]}")
            return None
        except Exception as e:
            log(f"    ! {type(e).__name__} {url.split('?')[0]}: {e}")
            return None
    return None


def get_json(url, conf, headers=None):
    body = http_get(url, conf, headers=headers)
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


TAG_RE = re.compile(r"<[^>]+>")
LASTFM_TAIL_RE = re.compile(r"\s*Read more on Last\.fm.*$", re.S)


def clean_text(s):
    """Plain text for a terminal: no markup, no hard wrapping, tidy blank lines.

    The schema promises body is plain text (docs/wiki.md), and every one of
    these services returns something else -- Last.fm sends HTML anchors, Genius
    sends escaped newlines, Wikipedia sends bracketed footnote markers.
    """
    if not s:
        return ""
    s = TAG_RE.sub("", s)
    s = LASTFM_TAIL_RE.sub("", s)
    s = s.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    s = re.sub(r"\[\d+\]", "", s)                 # wiki footnote markers
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# -------------------------------------------------------------------- sources

class Ctx:
    """What the sources have worked out about one track so far.

    Shared down the chain because the services are not independent: a
    MusicBrainz release id is what makes a Cover Art Archive lookup possible,
    and a Genius song id found for the story is the same id the credits come
    from. Passing it along turns a second lookup into a field access.
    """

    def __init__(self, conf, artist, album, title):
        self.conf = conf
        self.artist, self.album, self.title = artist, album, title
        self.mbid = {}
        self.release_mbid = None
        self.genius_song = None
        self.discogs_release = None
        self.lastfm_track = None


# --- MusicBrainz -------------------------------------------------------------

def mb_resolve(ctx):
    """Recording/artist/release MBIDs for the track, by text search.

    MPD carries no MusicBrainz ids (mpdclient.Song has none), so this is a
    fuzzy search and can be wrong. The score guard below is the only thing
    standing between a mistyped album tag and a confidently wrong story.
    """
    q = f'recording:"{ctx.title}" AND artist:"{ctx.artist}"'
    if ctx.album:
        q += f' AND release:"{ctx.album}"'
    url = f'{cfg(ctx.conf, "MUSICBRAINZ_API")}/recording/?query={urllib.parse.quote(q)}&fmt=json&limit=3'
    data = get_json(url, ctx.conf)
    if not data or not data.get("recordings"):
        return
    rec = data["recordings"][0]
    # MusicBrainz always returns its best guess, however poor. Below ~85 the
    # match is usually a different song with a word in common.
    if rec.get("score", 0) < 85:
        return
    ctx.mbid["recording"] = rec.get("id")
    credits = rec.get("artist-credit") or []
    if credits and credits[0].get("artist"):
        ctx.mbid["artist"] = credits[0]["artist"].get("id")
    for rel in rec.get("releases") or []:
        if rel.get("status") == "Official":
            ctx.release_mbid = rel.get("id")
            break
    if not ctx.release_mbid and rec.get("releases"):
        ctx.release_mbid = rec["releases"][0].get("id")
    ctx.mbid["release"] = ctx.release_mbid


def mb_bootlegs(ctx):
    """Releases of this recording that MusicBrainz marks as bootlegs."""
    if not ctx.mbid.get("recording"):
        return []
    url = f'{cfg(ctx.conf, "MUSICBRAINZ_API")}/recording/{ctx.mbid["recording"]}?inc=releases&fmt=json'
    data = get_json(url, ctx.conf)
    out = []
    for rel in (data or {}).get("releases", []):
        if rel.get("status") != "Bootleg":
            continue
        ev = (rel.get("release-events") or [{}])[0]
        area = (ev.get("area") or {}).get("name", "")
        out.append({k: v for k, v in {
            "title": rel.get("title"),
            "date": rel.get("date") or ev.get("date"),
            "city": area,
            "notes": rel.get("disambiguation"),
            "source": "MusicBrainz",
            "url": f'https://musicbrainz.org/release/{rel.get("id")}',
        }.items() if v})
    return out


def mb_url_rel(ctx, entity, mbid, rel_type):
    """A url relation of one type off a MusicBrainz entity (e.g. wikidata)."""
    if not mbid:
        return None
    url = f'{cfg(ctx.conf, "MUSICBRAINZ_API")}/{entity}/{mbid}?inc=url-rels&fmt=json'
    data = get_json(url, ctx.conf)
    for rel in (data or {}).get("relations", []):
        if rel.get("type") == rel_type:
            return (rel.get("url") or {}).get("resource")
    return None


# --- Wikipedia ---------------------------------------------------------------

def wiki_api(conf):
    return cfg(conf, "WIKIPEDIA_API").replace("{lang}", cfg(conf, "WIKI_LANG"))


def wikipedia_summary(ctx, page_title):
    url = f'{wiki_api(ctx.conf)}/api/rest_v1/page/summary/{urllib.parse.quote(page_title, safe="")}'
    data = get_json(url, ctx.conf)
    if not data or data.get("type") == "disambiguation":
        return None
    extract = clean_text(data.get("extract", ""))
    if not extract:
        return None
    return {"body": extract,
            "url": (data.get("content_urls", {}).get("desktop", {}) or {}).get("page", ""),
            "title": data.get("title", page_title),
            # Wikipedia's one-line "short description" -- "2007 single by Amy
            # Winehouse", "2011 Indian film". The cheapest reliable signal for
            # what kind of thing an article is about.
            "description": (data.get("description") or "")}


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# Titles that are indexes rather than subjects. A discography or a "list of
# songs sung by X" names the artist in every other sentence, so an
# artist-mentioned check waves them straight through -- which is exactly how a
# track ends up with a bare list as its story.
LIST_TITLE_RE = re.compile(r"(^list of |discography|filmography|^songs recorded by )", re.I)
SONG_WORDS = ("song", "single", "track", "composed", "recorded", "released as")


def _is_song_article(ctx, got):
    """Is this article about *this track*, rather than merely mentioning it?

    Three things have to line up: it must not be an index page, the article's
    title must actually contain the track's name, and either its short
    description or its opening must describe a song. Any one of these alone
    lets something through -- the title check alone accepts the film an
    Indian film song appears in, and the song-word check alone accepts an
    artist biography that happens to discuss their singles.
    """
    title, desc, body = got["title"], got["description"], got["body"]
    if LIST_TITLE_RE.search(title) or body.lower().startswith("the following is a list"):
        return False

    want = _norm(ctx.title)
    if not want or want not in _norm(title):
        return False

    blurb = (desc + " " + body[:400]).lower()
    if not any(w in blurb for w in SONG_WORDS):
        return False

    artist = _norm(ctx.artist.split(",")[0])
    return bool(artist) and artist in _norm(blurb)


def wikipedia_story(ctx):
    """The track's own Wikipedia article, if it has one.

    Searched rather than guessed at: "Smooth Operator" is a disambiguation
    page and "Smooth Operator (song)" is not, and no title-munging rule gets
    that right across a library. Most tracks have no article at all -- for
    those, returning nothing is the correct answer, and considerably more
    useful than the artist's biography wearing the track's name.
    """
    q = f'{ctx.title} {ctx.artist} song'
    url = (f'{wiki_api(ctx.conf)}/w/api.php?action=query&list=search&format=json'
           f'&srlimit=5&srsearch={urllib.parse.quote(q)}')
    data = get_json(url, ctx.conf)
    for hit in (data or {}).get("query", {}).get("search", []):
        got = wikipedia_summary(ctx, hit["title"])
        if got and _is_song_article(ctx, got):
            return got
    return None


def wikipedia_artist(ctx):
    """The artist's own article -- background, and labelled as such.

    Never the track's story: it is returned separately so build() can put it
    under its own heading. An artist biography presented as "the story behind
    this track" is worse than an empty modal, because it looks like an answer.
    """
    wd = mb_url_rel(ctx, "artist", ctx.mbid.get("artist"), "wikidata")
    if not wd:
        return None
    qid = wd.rstrip("/").rsplit("/", 1)[-1]
    url = (f'https://www.wikidata.org/w/api.php?action=wbgetentities&format=json'
           f'&props=sitelinks&ids={qid}&sitefilter={cfg(ctx.conf, "WIKI_LANG")}wiki')
    data = get_json(url, ctx.conf)
    links = (((data or {}).get("entities", {}).get(qid, {})).get("sitelinks", {})
             .get(f'{cfg(ctx.conf, "WIKI_LANG")}wiki', {}))
    if not links.get("title"):
        return None
    got = wikipedia_summary(ctx, links["title"])
    if not got or LIST_TITLE_RE.search(got["title"]):
        return None
    # The artist MBID came out of a fuzzy recording search, so it can belong to
    # somebody else entirely -- a track tagged Shreya Ghoshal resolved to
    # Sukhwinder Singh here. A biography under the wrong name is the worst
    # thing this script can produce, so the article has to name the artist we
    # were actually asked about.
    if _norm(ctx.artist.split(",")[0]) not in _norm(got["title"] + " " + got["body"][:300]):
        return None
    return got


# --- Last.fm -----------------------------------------------------------------

def lastfm_call(ctx, method, **params):
    key = ctx.conf.get("LASTFM_API_KEY")
    if not key:
        return None
    qs = urllib.parse.urlencode({"method": method, "api_key": key, "format": "json", **params})
    return get_json(f'{cfg(ctx.conf, "LASTFM_API")}?{qs}', ctx.conf)


def lastfm_track(ctx):
    if ctx.lastfm_track is None:
        data = lastfm_call(ctx, "track.getInfo", artist=ctx.artist, track=ctx.title, autocorrect=1)
        ctx.lastfm_track = (data or {}).get("track") or {}
    return ctx.lastfm_track


def lastfm_story(ctx):
    t = lastfm_track(ctx)
    body = clean_text(((t.get("wiki") or {}).get("content") or ""))
    if not body:
        return None
    return {"body": body, "url": t.get("url", "")}


# --- Genius ------------------------------------------------------------------

def genius_song(ctx):
    """The Genius song record, looked up once and reused.

    Genius search is forgiving to the point of unhelpfulness, so the hit is
    only accepted when its primary artist name overlaps the tag we searched
    with.
    """
    if ctx.genius_song is not None:
        return ctx.genius_song or None
    token = ctx.conf.get("GENIUS_TOKEN")
    if not token:
        ctx.genius_song = {}
        return None
    hdr = {"Authorization": f"Bearer {token}"}
    q = urllib.parse.quote(f"{ctx.artist} {ctx.title}")
    data = get_json(f'{cfg(ctx.conf, "GENIUS_API")}/search?q={q}', ctx.conf, headers=hdr)
    want = ctx.artist.split(",")[0].lower()
    for hit in (data or {}).get("response", {}).get("hits", []):
        res = hit.get("result") or {}
        primary = ((res.get("primary_artist") or {}).get("name") or "").lower()
        if want and want not in primary and primary not in want:
            continue
        full = get_json(f'{cfg(ctx.conf, "GENIUS_API")}/songs/{res["id"]}?text_format=plain',
                        ctx.conf, headers=hdr)
        ctx.genius_song = (full or {}).get("response", {}).get("song") or {}
        return ctx.genius_song or None
    ctx.genius_song = {}
    return None


def genius_story(ctx):
    song = genius_song(ctx)
    if not song:
        return None
    body = clean_text(((song.get("description") or {}).get("plain") or ""))
    # Genius writes "?" into the description when it has none.
    if not body or body == "?":
        return None
    return {"body": body, "url": song.get("url", "")}


def genius_credits(ctx):
    """Producers and writers as a behind-the-scenes block."""
    song = genius_song(ctx)
    if not song:
        return None
    def names(key):
        return [a.get("name") for a in (song.get(key) or []) if a.get("name")]
    lines = []
    for label, key in (("Produced by", "producer_artists"), ("Written by", "writer_artists")):
        got = names(key)
        if got:
            lines.append(f"{label} {', '.join(got)}.")
    if song.get("release_date_for_display"):
        lines.append(f'Released {song["release_date_for_display"]}.')
    if not lines:
        return None
    return {"body": " ".join(lines), "url": song.get("url", "")}


# --- Discogs -----------------------------------------------------------------

def discogs_release(ctx):
    if ctx.discogs_release is not None:
        return ctx.discogs_release or None
    token = ctx.conf.get("DISCOGS_TOKEN")
    if not token:
        ctx.discogs_release = {}
        return None
    params = {"artist": ctx.artist, "track": ctx.title, "type": "release", "per_page": 3, "token": token}
    if ctx.album:
        params["release_title"] = ctx.album
    data = get_json(f'{cfg(ctx.conf, "DISCOGS_API")}/database/search?{urllib.parse.urlencode(params)}', ctx.conf)
    results = (data or {}).get("results") or []
    if not results:
        ctx.discogs_release = {}
        return None
    full = get_json(f'{cfg(ctx.conf, "DISCOGS_API")}/releases/{results[0]["id"]}?token={token}', ctx.conf)
    ctx.discogs_release = full or {}
    return ctx.discogs_release or None


def discogs_notes(ctx):
    rel = discogs_release(ctx)
    if not rel:
        return None
    body = clean_text(rel.get("notes") or "")
    if not body:
        return None
    return {"body": body, "url": rel.get("uri", "")}


# ---------------------------------------------------------------------- images

def save_image(url, ctx, dest_dir, stem, extra_headers=None):
    """Download one image, naming it by role as docs/wiki.md requires.

    Returns the filename written, or None. The extension comes from the served
    content type rather than the URL, because Cover Art Archive redirects to
    storage URLs that carry no useful suffix.
    """
    _throttle(url)
    req = urllib.request.Request(url, headers={"User-Agent": cfg(ctx.conf, "WIKI_USER_AGENT"),
                                               **(extra_headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
            ext = IMAGE_EXTS.get(ctype)
            if not ext:
                return None
            data = r.read()
    except urllib.error.HTTPError as e:
        # A 404 from Cover Art Archive just means nobody has uploaded art for
        # this release. Ordinary, and not worth a line that looks like a fault.
        if e.code != 404:
            log(f"    ! image {e.code} {url.split('?')[0]}")
        return None
    except Exception as e:
        log(f"    ! image {type(e).__name__}: {e}")
        return None
    if len(data) < 1024:          # a placeholder or an error page, not a picture
        return None
    name = stem + ext
    (dest_dir / name).write_bytes(data)
    return name


def fetch_images(ctx, dest_dir):
    """Cover first from the archive that has the real sleeve, then fallbacks."""
    images = []

    if ctx.release_mbid:
        url = f'{cfg(ctx.conf, "COVERART_API")}/release/{ctx.release_mbid}/front-500'
        name = save_image(url, ctx, dest_dir, "cover")
        if name:
            images.append({"file": name, "role": "cover", "caption": ctx.album or "Cover",
                           "source": "Cover Art Archive", "url": url})

    if not images:
        t = lastfm_track(ctx)
        covers = ((t.get("album") or {}).get("image") or [])
        best = next((i.get("#text") for i in reversed(covers) if i.get("#text")), None)
        if best:
            name = save_image(best, ctx, dest_dir, "cover")
            if name:
                images.append({"file": name, "role": "cover", "caption": ctx.album or "Cover",
                               "source": "Last.fm", "url": best})

    song = genius_song(ctx)
    if song:
        art = song.get("song_art_image_url")
        if art and not any(i["role"] == "cover" for i in images):
            name = save_image(art, ctx, dest_dir, "cover")
            if name:
                images.append({"file": name, "role": "cover", "caption": "Song art",
                               "source": "Genius", "url": art})
        photo = ((song.get("primary_artist") or {}).get("image_url"))
        if photo:
            name = save_image(photo, ctx, dest_dir, "artist")
            if name:
                images.append({"file": name, "role": "artist", "caption": ctx.artist,
                               "source": "Genius", "url": photo})
    return images


# ------------------------------------------------------------------- assembly

def first_of(chain):
    """First source in the chain that returned anything. No merging."""
    for name, fn in chain:
        try:
            got = fn()
        except Exception as e:
            log(f"    ! {name}: {type(e).__name__}: {e}")
            continue
        if got:
            got["source"] = name
            return got
    return None


def build(ctx, dest_dir, want_images=True):
    """Assemble one track's wiki.json, or None if no source knew anything."""
    mb_resolve(ctx)

    story = first_of([
        ("Wikipedia", lambda: wikipedia_story(ctx)),
        ("Last.fm", lambda: lastfm_story(ctx)),
        ("Genius", lambda: genius_story(ctx)),
        ("Discogs", lambda: discogs_notes(ctx)),
    ])

    # Only worth the extra requests when nothing told us about the track
    # itself. Kept out of the chain above on purpose: it is background about
    # the artist, and passing it off as the story behind the track would be
    # worse than an empty modal, because it looks like an answer.
    artist_bio = None
    if not story:
        got = wikipedia_artist(ctx)
        if got:
            artist_bio = {"body": got["body"], "url": got.get("url", ""), "source": "Wikipedia"}

    behind = first_of([
        ("Genius", lambda: genius_credits(ctx)),
        ("Discogs", lambda: discogs_notes(ctx)),
    ])
    # The same Discogs notes must not be both the story and the behind-the-
    # scenes block: one source, one place.
    if behind and story and behind["body"] == story["body"]:
        behind = None

    bootlegs = mb_bootlegs(ctx)
    images = fetch_images(ctx, dest_dir) if want_images else []

    if not (story or artist_bio or behind or bootlegs or images):
        return None

    doc = {
        "schema": SCHEMA_VERSION,
        "track": {k: v for k, v in {
            "title": ctx.title, "artist": ctx.artist, "album": ctx.album,
            "mbid": {k2: v2 for k2, v2 in ctx.mbid.items() if v2} or None,
        }.items() if v},
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if story:
        doc["story"] = {"summary": story["body"]}
    if artist_bio:
        # Its own heading, under story.sections rather than as the summary, so
        # the modal can never present it as being about the track.
        doc.setdefault("story", {}).setdefault("sections", []).append(
            {k: v for k, v in {
                "heading": f"About {ctx.artist}",
                "body": artist_bio["body"],
                "source": "Wikipedia",
                "url": artist_bio.get("url"),
            }.items() if v})
    if behind:
        doc["behind_the_scenes"] = [{k: v for k, v in {
            "heading": "Credits" if behind["source"] == "Genius" else "Release notes",
            "body": behind["body"], "source": behind["source"], "url": behind.get("url"),
        }.items() if v}]
    if bootlegs:
        doc["bootlegs"] = bootlegs
    if images:
        doc["images"] = images

    seen, sources = set(), []
    for name, url in [(story and story["source"], story and story.get("url")),
                      (artist_bio and "Wikipedia", artist_bio and artist_bio.get("url")),
                      (behind and behind["source"], behind and behind.get("url")),
                      ("MusicBrainz" if bootlegs else None, None)] + \
                     [(i["source"], None) for i in images]:
        if name and name not in seen:
            seen.add(name)
            sources.append({k: v for k, v in {"name": name, "url": url,
                                              "fetched_at": doc["fetched_at"]}.items() if v})
    if sources:
        doc["sources"] = sources
    return doc


# ----------------------------------------------------------------- validation

IMAGE_NAME_RE = re.compile(r"^(cover|artist|scene-\d{2}|bootleg-\d{2}|extra-\d{2})\.(jpg|jpeg|png|webp)$")


def validate(doc):
    """The schema's rules, checked before anything reaches disk.

    Deliberately not a jsonschema dependency: this script is stdlib-only like
    the rest of bin/, and a bad file that is never written is much easier to
    deal with than one that has to be found again later. schema/wiki.schema.json
    remains the full contract -- docs/wiki.md shows how to run it over the tree.
    """
    problems = []
    if doc.get("schema") != SCHEMA_VERSION:
        problems.append(f'schema must be {SCHEMA_VERSION}')
    for key in ("title", "artist"):
        if not doc.get("track", {}).get(key):
            problems.append(f"track.{key} is required")
    if not doc.get("fetched_at"):
        problems.append("fetched_at is required")
    for i, img in enumerate(doc.get("images", [])):
        if not IMAGE_NAME_RE.match(img.get("file", "")):
            problems.append(f'images[{i}].file {img.get("file")!r} breaks the naming rule')
        if not img.get("file", "").startswith(img.get("role", "\0")):
            problems.append(f'images[{i}] role {img.get("role")!r} disagrees with its filename')
    for i, b in enumerate(doc.get("bootlegs", [])):
        if not b.get("title"):
            problems.append(f"bootlegs[{i}].title is required")
    return problems


# ---------------------------------------------------------------------- sweep

def ffprobe_tags(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return json.loads(r.stdout).get("format", {}).get("tags", {})
    except Exception as e:
        log(f"ffprobe error {path}: {e}")
    return {}


def tag(tags, *names):
    for n in names:
        for k in (n, n.upper(), n.lower(), n.capitalize(), n.title()):
            if tags.get(k):
                return tags[k].strip()
    return ""


def prune_empty(leaf, stop):
    """Remove leaf and any parents it emptied, never climbing past stop."""
    d = leaf
    while d != stop and stop in d.parents:
        try:
            d.rmdir()
        except OSError:      # not empty, or gone already -- either way, stop
            return
        d = d.parent


def iter_tracks(music_dir, only):
    base = music_dir / only if only else music_dir
    if not base.exists():
        return
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            yield p


def main():
    conf = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--music", default=conf.get("MUSIC_DIR"), help="music library root")
    ap.add_argument("--wiki", default=None, help="wiki collection root")
    ap.add_argument("--reports", default=None,
                    help="where no-data.txt lives (default: beside --wiki)")
    ap.add_argument("--only", default="", help="restrict to a subtree, e.g. sade/the-essential-rnb")
    ap.add_argument("--limit", type=int, default=int(cfg(conf, "WIKI_BATCH") or 100),
                    help="tracks to fetch this run (0 = no limit)")
    ap.add_argument("--force", action="store_true", help="refetch tracks that already have a wiki.json")
    ap.add_argument("--retry-missing", action="store_true", help="retry tracks previously recorded as having no data")
    ap.add_argument("--no-images", action="store_true", help="text only")
    ap.add_argument("--dry-run", action="store_true", help="list what would be fetched and stop")
    args = ap.parse_args()

    if not args.music:
        sys.exit("set MUSIC_DIR in ~/.config/music-tui/config, or pass --music")
    music = expand(args.music)

    # --wiki carries the reports dir with it. They are one collection: pointing
    # the wiki somewhere else for a trial run and still appending "skip this
    # track" to the real repo means a throwaway run quietly teaches the real
    # one to skip tracks it never actually wrote anywhere.
    if args.wiki:
        wiki = expand(args.wiki)
        reports = expand(args.reports) if args.reports else wiki.parent / "wiki-reports"
    else:
        wiki = expand(conf.get("WIKI_DIR") or (expand(conf.get("MUSIC_METADATA_DIR", "~")) / "wiki"))
        reports = expand(args.reports or conf.get("WIKI_REPORTS_DIR") or (wiki.parent / "wiki-reports"))
    no_data_file = reports / "no-data.txt"
    no_data = set()
    if no_data_file.is_file() and not args.retry_missing:
        no_data = {l.strip() for l in no_data_file.read_text(encoding="utf-8").splitlines() if l.strip()}

    todo = []
    for p in iter_tracks(music, args.only):
        rel = p.relative_to(music)
        dest = wiki / rel.parent / rel.stem
        if not args.force and (dest / "wiki.json").is_file():
            continue
        if str(rel.with_suffix("")) in no_data:
            continue
        todo.append((p, rel, dest))

    total = len(todo)
    batch = todo[: args.limit] if args.limit > 0 else todo
    log(f"{total} track(s) without a wiki; this run takes {len(batch)}")

    if args.dry_run:
        for _, rel, _ in batch:
            print(rel.with_suffix(""))
        remaining = total - len(batch)
        log(f"dry run -- nothing written. {remaining} would remain for the next run.")
        return 0

    written = empty = failed = 0
    fresh_no_data = []
    for n, (path, rel, dest) in enumerate(batch, 1):
        tags = ffprobe_tags(path)
        artist = tag(tags, "artist", "album_artist", "ALBUMARTIST")
        title = tag(tags, "title")
        album = tag(tags, "album")
        log(f"[{n}/{len(batch)}] {rel}")
        if not artist or not title:
            log("    - no artist/title tag, skipped")
            failed += 1
            continue

        dest.mkdir(parents=True, exist_ok=True)
        ctx = Ctx(conf, artist, album, title)
        try:
            doc = build(ctx, dest, want_images=not args.no_images)
        except Exception as e:
            log(f"    ! {type(e).__name__}: {e}")
            failed += 1
            continue

        if doc is None:
            log("    - nothing found")
            fresh_no_data.append(str(rel.with_suffix("")))
            empty += 1
            # Leave no empty directory behind to be mistaken for a fetched
            # track -- nor the album and artist directories that making it
            # created, or an album nobody has a single story for still looks
            # like a fetched album.
            prune_empty(dest, wiki)
            continue

        doc["track"]["file"] = str(rel)
        problems = validate(doc)
        if problems:
            for p2 in problems:
                log(f"    ! invalid: {p2}")
            failed += 1
            continue

        (dest / "wiki.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
        bits = [k for k in ("story", "behind_the_scenes", "bootlegs", "images") if doc.get(k)]
        log(f"    + {', '.join(bits)}")
        written += 1

    if fresh_no_data:
        reports.mkdir(parents=True, exist_ok=True)
        with open(no_data_file, "a", encoding="utf-8") as fh:
            fh.write("\n".join(fresh_no_data) + "\n")

    remaining = total - len(batch)
    log(f"\nwrote {written}, nothing-found {empty}, failed {failed}")
    if remaining:
        log(f"{remaining} track(s) left -- run again when you want the next batch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
