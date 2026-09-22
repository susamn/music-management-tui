# wiki (track stories)

Per-track background — the story, how it was made, known bootlegs, and images —
fetched **ahead of time** into `music-metadata/wiki/`, pushed out to the music
collection, and read **offline** by mpdtui's story modal.

Nothing in mpdtui goes online. The fetching is a separate script you run when
you feel like it; the player only ever reads files that are already on disk.

## Layout

`wiki/` mirrors the library the way `lyrics/` does, one level deeper: a track
needs a *folder*, not a file, because it owns images as well as text.

```
music-metadata/
  lyrics/sade/the-essential-rnb/01-smooth-operator.lrc
  wiki/  sade/the-essential-rnb/01-smooth-operator/
                                  wiki.json
                                  cover.jpg
                                  artist.jpg
                                  scene-01.jpg
```

The directory is the track's filename **without its extension**, so
`01-smooth-operator.mp3` and `01-smooth-operator.flac` share one story, and two
tracks on the same album can never collide over a `cover.jpg`.

### Pushing to the music collection

The push inserts a `wiki/` level so album folders keep their shape — one wiki
folder per album rather than one folder per track sitting among the audio:

```
wiki/<artist>/<album>/<track>/        ->   MUSIC_DIR/<artist>/<album>/wiki/<track>/
```

That is `dirname` + `wiki/` + `basename`, which is the only place the two trees
differ. Unlike lyrics, this is not a straight relative-path copy.

`bin/wiki-push.py` — menu **2·5–2·7**, the same shape as `lyrics-push.py`:

```bash
wiki-push.py list                      # track dirs git hasn't committed yet
wiki-push.py list | fzf -m | wiki-push.py to-music
wiki-push.py list | fzf -m | wiki-push.py to-drive
```

The unit is the **directory**, not the file: a track's `wiki.json` and its
images are pushed together or not at all, and an existing destination is
replaced rather than merged, so a stale image from an earlier fetch cannot
outlive the manifest that named it. Overwriting asks once.

A track whose audio is not actually at that path is skipped and reported — an
orphaned or misfiled wiki directory is worth hearing about rather than
silently copying to a place nothing will read it from. Nothing is moved,
staged, committed or deleted; the music-metadata working tree is untouched.

## Fetching

`bin/wiki-fetch.py` — menu **2·1–2·4**. Stdlib only; needs `ffprobe` on PATH.

Every field falls down a chain of sources and takes the **first that has
anything**. Nothing is merged, so a field always has exactly one provenance and
the file can never end up quoting two services against each other:

```
story              wikipedia -> lastfm -> genius -> discogs -> wikipedia (artist)
behind_the_scenes  genius (credits) -> discogs (release notes)
bootlegs           musicbrainz
images             coverartarchive -> lastfm -> genius
```

A source with no API key configured is skipped and the chain moves on, so a
machine with no keys at all still gets MusicBrainz, Wikipedia and Cover Art
Archive.

### Batches

A run takes `--limit` tracks (default `$WIKI_BATCH`, 100) and stops, so one run
is a known quantity of network traffic. Run it again for the next batch:

```bash
bin/wiki-fetch.py --dry-run          # what the next batch would be
bin/wiki-fetch.py                    # fetch it
bin/wiki-fetch.py --only sade        # restrict to a subtree
```

Resuming needs no bookkeeping: a track with a `wiki.json` is skipped. A track
**no source knew anything about** is recorded in `$WIKI_REPORTS_DIR/no-data.txt`
and skipped on later sweeps too, so a hundred-track run is a hundred *new*
tracks rather than the same failures again — `--retry-missing` tries them anyway.
Nothing is left on disk for such a track, not even the album directory the
attempt created.

`--wiki` carries the reports directory with it (override with `--reports`).
They are one collection: pointing the wiki somewhere else for a trial run while
still appending "skip this track" to the real repo would let a throwaway run
quietly teach the real one to skip tracks it never wrote anywhere.

### Matching, and getting it wrong

MPD carries no MusicBrainz ids, so every lookup starts from the artist/album/
title tags and is a fuzzy search. Three guards keep a bad match out:

- MusicBrainz results below a score of 85 are dropped.
- A Wikipedia article is accepted only if its text names the artist — which is
  what stops `Smooth Operator` landing on the disambiguation page.
- A Genius hit is accepted only if its primary artist overlaps the tag.

They are not perfect. A wrong story is a wrong `wiki.json`: delete the directory
and the next sweep picks the track up again.

### Rate limits

MusicBrainz documents one request per second and answers a sustained sweep with
`503` regardless; those are retried with a widening pause rather than treated as
"no data", since otherwise a long run quietly loses most of its bootlegs and
cover art. Requests are throttled per host, so a chain touching four services is
not serialised behind the slowest one's limit.

Every request carries `$WIKI_USER_AGENT`. MusicBrainz **rejects** a generic one
— that is what a sudden wall of 503s with no retries means.

### Config

Added to `~/.config/music-tui/config` (`config.example` is the template; an
existing config gains new keys on the next `music-tui.sh` run):

| Key | |
|---|---|
| `WIKI_DIR` | the wiki collection, default `$MUSIC_METADATA_DIR/wiki` |
| `WIKI_REPORTS_DIR` | where `no-data.txt` lives |
| `WIKI_BATCH` | tracks per run (100) |
| `WIKI_LANG` | Wikipedia language edition (`en`) |
| `WIKI_USER_AGENT` | sent on every request |
| `MUSICBRAINZ_API` etc. | endpoints, so they can be repointed without touching the script |
| `LASTFM_API_KEY`, `GENIUS_TOKEN`, `DISCOGS_TOKEN` | optional; blank disables that source |

## wiki.json

Validated by `schema/wiki.schema.json` (JSON Schema draft 2020-12);
`schema/wiki.example.json` is a filled-in one to copy from.

| Key | | What it is |
|---|---|---|
| `schema` | required | Always `1`. A reader that does not know the version refuses the file rather than guessing. |
| `track` | required | `title`, `artist` required; `album`, `year`, `file`, `mbid` optional. Identifies the subject so a misfiled directory is detectable. |
| `fetched_at` | required | RFC 3339 UTC. The modal shows it, so stale text looks stale. |
| `story` | | `summary` (opens the modal) plus ordered `sections`. |
| `behind_the_scenes` | | Recording, production, sampling, session players — how it got made. |
| `bootlegs` | | One entry per unofficial recording: `title` required, then `date`, `venue`, `city`, `format`, `notes`. |
| `images` | | Manifest of the image files in this directory, in display order. |
| `links` | | Further reading, listed at the end. |
| `sources` | | One entry per service consulted, with `license` — CC BY-SA text has to be attributed. |

Every prose block is `{heading?, body, source?, url?}`.

**`body` is plain text.** Not HTML, not Markdown. The modal wraps it to the
terminal and separates paragraphs on blank lines; anything else shows up as
literal angle brackets and asterisks.

## Image files

The filename carries the role, so a reader can group images sensibly even with
no manifest — and `images[]` adds the caption and credit a filename cannot hold.
The two must agree; the schema enforces it.

```
cover.jpg       the release/single sleeve
artist.jpg      the artist or band
scene-01.jpg    behind the scenes: studio, sessions, live
bootleg-01.jpg  bootleg sleeve or tape art
extra-01.jpg    anything else
```

- `.jpg`, `.jpeg`, `.png` or `.webp`. No `.gif`.
- Numbered from `01`, zero-padded to two digits, no gaps.
- Flat — the track directory has no subdirectories.
- `cover` and `artist` are singular; the other three are series.

Keep them to roughly **1000px on the long edge**. These are drawn in a terminal
— either as pixels via the Kitty graphics protocol or downsampled to blocks — so
anything larger is bytes spent to be thrown away, multiplied by every track.

## Writing for a terminal modal

The modal is perhaps 60–80 columns wide and scrolls with `j`/`k`.

- A `summary` of one or two paragraphs is the right size. It is what most
  readings will consist of.
- Prefer several short sections to one long one; each `heading` is a place the
  eye can stop.
- Do not hand-wrap `body`. The modal wraps to the actual terminal width, and
  pre-wrapped text wraps twice.
- Omit a key entirely rather than setting it to `""` or `[]`. The modal skips
  absent sections; an empty one becomes a heading with nothing under it.

## Validating

```bash
python3 -m venv .venv && .venv/bin/pip install jsonschema
.venv/bin/python - <<'PY'
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator

schema = json.load(open("schema/wiki.schema.json"))
V = Draft202012Validator(schema)
bad = 0
for f in Path("wiki").rglob("wiki.json"):
    for e in V.iter_errors(json.load(open(f))):
        print(f"{f}: {list(e.path)}: {e.message}")
        bad += 1
print("clean" if not bad else f"{bad} problem(s)")
sys.exit(1 if bad else 0)
PY
```

A fetch script should validate before writing, not after: a rejected file never
reaching disk is much easier to reason about than one that has to be found again
later.
