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
