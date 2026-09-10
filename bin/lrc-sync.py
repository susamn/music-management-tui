#!/usr/bin/env python3
"""lrc-sync - manually time .txt lyrics into .lrc against MPD playback.

Flow (see README.md):
  * The tool follows whatever track MPD is playing.
  * It looks for lyrics/<same-relative-path>.txt next to this repo.
  * No .txt        -> [p] paste one ($EDITOR), saved at the mirrored path.
  * .txt present   -> [e] edit it ($EDITOR).
  * .txt, no .lrc  -> [c] manual sync: [space] restarts the track at 0:00 (a
    real MPD seek+play), zeroes a self-run clock, and stamps the landing line
    at 0:00 - all one action. Each 'n' after that stamps the moment you ENTER
    the next line (not the moment you leave the current one), minus a fixed
    STAMP_OFFSET to compensate for reaction time. Timing is that self-run
    clock, NOT MPD's reported elapsed - it can sit seconds behind/ahead of
    what you actually hear, and a human "zero it by ear" button just adds
    reaction-time error on top of that.
  * .txt and .lrc  -> [o] same sync; old .lrc -> .lrc.old on save.
  * The sync always opens with a blank landing line before line 1 (added only
    if the .txt doesn't already start with one) - a slot to press 'n' on for
    the song's intro, so the first real lyric doesn't inherit that gap.
  * [h]/[l] seek -/+5s, [H]/[L] -/+15s (clock shifts with the audio). [g]
    seeks to the cursor line's own stamp, to redo one line without a full pass.
  * During a sync the track is pinned: MPD is set to stop at end of song
    (single on) and, as a belt-and-suspenders, the tool force-stops MPD the
    moment it ever detects a different song became current. Prior
    single/repeat/consume restored on leaving.
  * Sync progress is kept per track for the whole session - leave and resume
    with [c]/[o]; editing the .txt keeps timestamps on unchanged lines.

Config:
  MUSIC_METADATA_DIR  the music-metadata repo; lyrics land in its lyrics/ .
                      (--lyrics DIR overrides. Falls back to ../.. of this file.)
  MPD_HOST / MPD_PORT  mpc convention. Default localhost:6600.
Stdlib only.
"""
import argparse
import curses
import locale
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

locale.setlocale(locale.LC_ALL, "")

_ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
_ap.add_argument("--lyrics", metavar="DIR",
                 help="lyrics dir to read/write (default: $MUSIC_METADATA_DIR/lyrics)")
_args = _ap.parse_args()

if _args.lyrics:
    LYRICS_DIR = Path(_args.lyrics).expanduser().resolve()
elif os.environ.get("MUSIC_METADATA_DIR"):
    LYRICS_DIR = Path(os.environ["MUSIC_METADATA_DIR"]).expanduser().resolve() / "lyrics"
else:
    LYRICS_DIR = Path(__file__).resolve().parents[2] / "lyrics"
REPO = LYRICS_DIR.parent
AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".wma", ".aac", ".alac"}
# Every 'n' stamp is taken a bit after the line actually starts - your reaction
# time to hearing it. Subtract a fixed amount to pull the stamp back towards
# the true onset. Tune this if your own reaction time runs faster/slower.
STAMP_OFFSET = 0.5
# Seek step sizes for h/l (small) and H/L (big), in seconds.
SEEK_SMALL = 5.0
SEEK_BIG = 15.0


# --------------------------------------------------------------------------- MPD
class MPDError(Exception):
    pass


class MPD:
    def __init__(self):
        host = os.environ.get("MPD_HOST", "localhost")
        port = int(os.environ.get("MPD_PORT", "6600"))
        password = None
        if "@" in host:
            password, host = host.split("@", 1)
        self.sock = socket.create_connection((host, port), timeout=5)
        self.fp = self.sock.makefile("rw", encoding="utf-8", newline="\n")
        if not self.fp.readline().startswith("OK MPD"):
            raise MPDError("not an MPD server")
        if password:
            self.cmd(f'password "{password}"')

    def cmd(self, command):
        self.fp.write(command + "\n")
        self.fp.flush()
        out = {}
        while True:
            line = self.fp.readline()
            if not line:
                raise MPDError("connection closed")
            line = line.rstrip("\n")
            if line == "OK":
                return out
            if line.startswith("ACK"):
                raise MPDError(line)
            key, _, value = line.partition(": ")
            out.setdefault(key, value)

    def close(self):
        try:
            self.fp.write("close\n")
            self.fp.flush()
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


# ----------------------------------------------------------------------- helpers
def fmt_time(seconds):
    if seconds is None:
        return "--:--.--"
    cs = int(round(seconds * 100))
    return f"{cs // 6000:02d}:{cs % 6000 // 100:02d}.{cs % 100:02d}"


def paths_for(rel):
    stem = LYRICS_DIR / Path(rel)
    return stem.with_suffix(".txt"), stem.with_suffix(".lrc")


def is_audio(rel):
    return rel is not None and Path(rel).suffix.lower() in AUDIO_EXTS


def run_editor(stdscr, initial=""):
    editor = shlex.split(os.environ.get("EDITOR", "vi"))
    fd, name = tempfile.mkstemp(suffix=".txt", prefix="lrc-sync-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(initial)
        curses.def_prog_mode()
        curses.endwin()
        subprocess.call(editor + [name])
        curses.reset_prog_mode()
        curses.curs_set(0)
        stdscr.keypad(True)
        stdscr.clear()
        return Path(name).read_text(encoding="utf-8")
    finally:
        os.unlink(name)


def rel_repo(path):
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


C = {}  # colour attributes, filled in by init_colors()

# Per-track sync state, kept for the whole session so a sync can be resumed
# after leaving the sync screen. rel -> {"times": [...], "texts": [...], "cur": int}
HISTORY = {}


def init_colors():
    if not curses.has_colors():
        return {}
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    wide = curses.COLORS >= 256
    orange = 208 if wide else curses.COLOR_YELLOW
    red = 196 if wide else curses.COLOR_RED
    curses.init_pair(1, curses.COLOR_GREEN, bg)
    curses.init_pair(2, orange, bg)
    curses.init_pair(3, red, bg)
    return {
        "green": curses.color_pair(1),
        "orange": curses.color_pair(2),
        "red": curses.color_pair(3),
    }


def addline(stdscr, y, x, text, attr=0):
    h, w = stdscr.getmaxyx()
    if 0 <= y < h and x < w:
        try:
            stdscr.addnstr(y, x, text, w - x - 1, attr)
        except curses.error:
            pass


def addsegs(stdscr, y, segments):
    x = 0
    for text, attr in segments:
        addline(stdscr, y, x, text, attr)
        x += len(text)


# ------------------------------------------------------------------- save an lrc
def save_lrc(lines, times, lrc_path):
    stamped = [(times[i], lines[i]) for i in range(len(lines)) if times[i] is not None]
    if not stamped:
        return "No lines stamped - nothing saved."
    note = ""
    if lrc_path.exists():
        old = Path(str(lrc_path) + ".old")
        if old.exists():
            old = Path(f"{lrc_path}.old.{int(time.time())}")
        lrc_path.rename(old)
        note = f"  (old .lrc -> {old.name})"
    lrc_path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"[{fmt_time(t)}] {text}\n" for t, text in stamped)
    lrc_path.write_text(body, encoding="utf-8")
    return f"Saved {rel_repo(lrc_path)} - {len(stamped)} lines.{note}"


# ------------------------------------------------------------------- sync screen
SYNC_MODES = (("single", "1"), ("repeat", "0"), ("consume", "0"))


def sync_screen(stdscr, mpd, rel):
    txt_path, lrc_path = paths_for(rel)
    lines = txt_path.read_text(encoding="utf-8").splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return "The .txt file is empty - nothing to sync."
    # Always give the sync a leading blank "landing" line, whether or not the
    # .txt itself opens with one. Most songs have a few seconds of intro
    # before the first lyric, and without a slot to press 'n' on for that gap
    # you're forced to stamp the intro's tail onto the first real line. This
    # is display/output-only - the .txt file on disk is never touched.
    if lines[0].strip():
        lines = [""] + lines

    # Resume from this session's history, keeping timestamps for lines whose
    # text still matches (so editing the .txt only drops the lines you changed).
    prev = HISTORY.get(rel)
    if prev:
        old_t, old_x = prev["times"], prev["texts"]
        times = [old_t[i] if i < len(old_x) and old_x[i] == lines[i] else None
                 for i in range(len(lines))]
        cur = min(max(prev["cur"], 0), len(lines) - 1)
        kept = sum(t is not None for t in times)
        hint = (f"Resumed - {kept} line(s) stamped, cursor at your last entered "
                "line. Press [space] then 'n' to continue (or ↑/↓ to jump first).")
    else:
        times = [None] * len(lines)
        cur = 0
        hint = "Press [space] to restart the track at 0:00 and zero the clock."

    def remember():
        HISTORY[rel] = {"times": list(times), "texts": list(lines), "cur": cur}

    # Pin MPD to this track: capture its queue id (for the restart command and
    # the drift check below), and don't let it roll on to the next song mid-sync.
    try:
        cs0 = mpd.cmd("currentsong")
        pinned_id = cs0.get("Id")
    except MPDError:
        pinned_id = None
    try:
        st0 = mpd.cmd("status")
        saved_modes = [(k, st0.get(k, "0")) for k, _ in SYNC_MODES]
        for k, v in SYNC_MODES:
            mpd.cmd(f"{k} {v}")
    except MPDError:
        saved_modes = []

    # We do NOT trust MPD's reported `elapsed` for stamping - it can sit seconds
    # off from the sound actually leaving the speakers, and a human "zero it by
    # ear" button just adds reaction-time error on top of that. Instead the tool
    # itself restarts the track and zeroes a plain monotonic clock in the same
    # action ([space] -> restart_and_zero below), so nobody has to judge it.
    clock_t0 = None      # monotonic() when the song was set to 0:00, or None
    paused_accum = 0.0   # seconds the clock was frozen for (MPD not playing)
    pause_mark = None     # monotonic() when the current freeze began, or None

    def position():
        if clock_t0 is None:
            return None
        p = time.monotonic() - clock_t0 - paused_accum
        if pause_mark is not None:
            p -= time.monotonic() - pause_mark
        return max(0.0, p)

    def restart_and_zero():
        nonlocal clock_t0, paused_accum, pause_mark
        if pinned_id is not None:
            try:
                mpd.cmd(f"playid {pinned_id}")
                mpd.cmd("seekcur 0")
            except MPDError:
                pass
        clock_t0 = time.monotonic()
        paused_accum = 0.0
        pause_mark = None
        # Restarting the track *is* entering the landing line - stamp it at
        # 0:00 right away, no separate 'n' needed for line 0.
        times[0] = 0.0

    def seek_to(target):
        # Move both the audio and the internal clock to `target` seconds.
        # The clock is authoritative for stamping, so we shift clock_t0 by
        # exactly the change in position() - MPD's resulting elapsed may
        # differ slightly and that's fine, same as everywhere else here.
        nonlocal clock_t0
        if clock_t0 is None:
            return None
        old = position()
        new = max(0.0, target)
        try:
            mpd.cmd(f"seekcur {new:.2f}")
        except MPDError:
            pass
        clock_t0 -= (new - old)
        return new

    stdscr.nodelay(True)
    result = "Aborted - nothing saved."
    try:
        while True:
            try:
                st = mpd.cmd("status")
                mpd_elapsed = float(st.get("elapsed", 0) or 0)
                state = st.get("state", "?")
                cur_id = st.get("songid")
            except MPDError:
                mpd_elapsed, state, cur_id = 0.0, "no-mpd", None

            # Belt-and-suspenders: if MPD ever ends up on a different song than
            # the one pinned for this sync - end of track, single-mode not
            # honoured, someone hit "next" on another client - stop it dead
            # rather than let it keep playing something else.
            if pinned_id is not None and cur_id is not None and cur_id != pinned_id:
                try:
                    mpd.cmd("stop")
                except MPDError:
                    pass
                state = "stop"

            # Freeze the clock whenever MPD is not actually playing.
            if clock_t0 is not None:
                if state in ("pause", "stop") and pause_mark is None:
                    pause_mark = time.monotonic()
                elif state == "play" and pause_mark is not None:
                    paused_accum += time.monotonic() - pause_mark
                    pause_mark = None

            pos = position()

            stdscr.erase()
            h, w = stdscr.getmaxyx()
            addline(stdscr, 0, 0, "LRC SYNC", curses.A_BOLD)
            addline(stdscr, 0, 10, rel)
            shown = min(cur + 1, len(lines))
            if pos is None:
                clock = "clock --:--.--  (not started)"
            else:
                clock = f"clock {fmt_time(pos)}"
            addline(stdscr, 1, 0,
                    f"{clock}   mpd {state} {fmt_time(mpd_elapsed)}   line {shown}/{len(lines)}")
            addline(stdscr, 2, 0,
                    "[space] restart+zero clock  [n] enter next line  [↑/↓] move  "
                    "[b] undo  [s] save  [ESC] leave",
                    curses.A_DIM)
            addline(stdscr, 3, 0,
                    "[h/l] seek ∓5s   [H/L] seek ∓15s   [g] seek to this line's stamp",
                    curses.A_DIM)
            addline(stdscr, 4, 0, hint, curses.A_DIM)

            top = 6
            vis = max(1, h - top - 1)
            start = max(0, min(cur - vis // 2, len(lines) - vis))
            for row, i in enumerate(range(start, min(len(lines), start + vis))):
                stamp = fmt_time(times[i]) if times[i] is not None else "        "
                text = lines[i] if lines[i].strip() else "·"
                marker = ">" if i == cur else " "
                if i == cur:
                    attr = curses.A_REVERSE
                elif times[i] is not None:
                    attr = C.get("green", 0)
                else:
                    attr = 0
                addline(stdscr, top + row, 0, f"{marker} [{stamp}] {text}", attr)
            stdscr.refresh()

            ch = stdscr.getch()
            if ch == -1:
                time.sleep(0.05)
                continue
            if ch == ord(" "):
                # Restart the track at 0:00 and zero the clock in one action -
                # no ear-timing, the tool drives both.
                restart_and_zero()
                hint = "Restarted at 0:00 - clock zeroed. Press 'n' on each line."
                remember()
            elif ch in (ord("n"), ord("N")):
                if clock_t0 is None:
                    # First 'n' with no explicit [space]: fall back to zeroing
                    # here, without touching MPD (e.g. you're continuing a
                    # take that's already mid-playback). This press itself
                    # only establishes 0:00 for the landing line - press 'n'
                    # again to actually enter line 1, same as after [space].
                    clock_t0 = time.monotonic()
                    paused_accum = 0.0
                    pause_mark = None
                    times[0] = 0.0
                elif cur < len(lines) - 1:
                    # Stamp is taken on ENTERING the next line, not on leaving
                    # the current one - move first, then mark the moment.
                    # Pulled back by STAMP_OFFSET to compensate for reaction
                    # time between hearing the line start and pressing 'n'.
                    cur += 1
                    times[cur] = max(0.0, position() - STAMP_OFFSET)
                remember()
            elif ch in (curses.KEY_DOWN, ord("j")):
                cur = min(cur + 1, len(lines) - 1)
                remember()
            elif ch in (curses.KEY_UP, ord("k")):
                cur = max(cur - 1, 0)
                remember()
            elif ch in (ord("h"), ord("l"), ord("H"), ord("L")):
                step = SEEK_BIG if ch in (ord("H"), ord("L")) else SEEK_SMALL
                delta = -step if ch in (ord("h"), ord("H")) else step
                if clock_t0 is None:
                    hint = "Start the clock ([space]) before seeking."
                else:
                    new = seek_to(position() + delta)
                    hint = f"seek {'-' if delta < 0 else '+'}{abs(delta):.0f}s -> {fmt_time(new)}"
            elif ch in (ord("g"), ord("G")):
                # Jump the song to where the cursor line is stamped, to
                # re-check / re-time that one line without replaying from 0:00.
                if clock_t0 is None:
                    hint = "Start the clock ([space]) first."
                elif times[cur] is None:
                    hint = "This line has no stamp to jump to."
                else:
                    if pinned_id is not None:
                        try:
                            mpd.cmd(f"playid {pinned_id}")
                        except MPDError:
                            pass
                    new = seek_to(times[cur])
                    hint = f"jumped to line {cur + 1} @ {fmt_time(new)}"
            elif ch in (ord("b"), ord("B"), curses.KEY_BACKSPACE, 127, 8):
                # Undo the most recent entry: clear the line you're currently
                # on (the one 'n' just stamped) and step back to redo it.
                if cur > 0:
                    times[cur] = None
                    cur -= 1
                remember()
            elif ch in (ord("s"), ord("S")):
                result = save_lrc(lines, times, lrc_path)
                break
            elif ch == 27:  # ESC - leave, but keep history for resume
                result = "Left sync - resume any time with [c]/[o]."
                break
    finally:
        remember()
        for k, v in saved_modes:
            try:
                mpd.cmd(f"{k} {v}")
            except MPDError:
                pass
        stdscr.nodelay(False)
    return result


# ------------------------------------------------------------------- main screen
def main_screen(stdscr, mpd, msg=""):
    stdscr.nodelay(True)
    last_poll = 0.0
    cs, st = {}, {}

    while True:
        if time.time() - last_poll > 0.4:
            cs = mpd.cmd("currentsong")
            st = mpd.cmd("status")
            last_poll = time.time()

        rel = cs.get("file")
        playable = is_audio(rel)
        txt_path = lrc_path = None
        have_txt = have_lrc = False
        if playable:
            txt_path, lrc_path = paths_for(rel)
            have_txt = txt_path.exists()
            have_lrc = lrc_path.exists()

        stdscr.erase()
        h, _ = stdscr.getmaxyx()
        addline(stdscr, 0, 0, "lrc-sync - MPD lyrics timing tool", curses.A_BOLD)
        addline(stdscr, 2, 0, f"MPD: {st.get('state', 'stop')}")
        if playable:
            addline(stdscr, 3, 0, f"track: {rel}")
            art, tit = cs.get("Artist", ""), cs.get("Title", "")
            if art or tit:
                addline(stdscr, 4, 0, f"       {art} - {tit}")
            el = float(st.get("elapsed", 0) or 0)
            du = float(st.get("duration", 0) or 0)
            addline(stdscr, 5, 0, f"       {fmt_time(el)} / {fmt_time(du)}")
            if not have_txt:
                addsegs(stdscr, 7, [
                    ("  ✗ no .txt lyrics found - cannot proceed",
                     C.get("red", 0) | curses.A_BOLD)])
                addline(stdscr, 9, 0, "  [p] paste .txt lyrics  (opens $EDITOR)")
            else:
                lrc_seg = (("✓ .lrc found", C.get("orange", 0)) if have_lrc
                           else ("✗ .lrc not found", C.get("red", 0)))
                addsegs(stdscr, 7, [
                    ("  ", 0),
                    ("✓ .txt found", C.get("green", 0)),
                    ("      ", 0),
                    lrc_seg,
                ])
                resume = "  [resume available]" if rel in HISTORY else ""
                if have_lrc:
                    addline(stdscr, 9, 0,
                            "  [o] optimize .lrc sync  (manual sync)" + resume,
                            C.get("green", 0) if resume else 0)
                else:
                    addline(stdscr, 9, 0,
                            "  [c] convert .txt -> .lrc  (manual sync)" + resume,
                            C.get("green", 0) if resume else 0)
                addline(stdscr, 10, 0, "  [e] edit .txt lyrics  (opens $EDITOR)")
        elif cs.get("file"):
            addline(stdscr, 3, 0, f"stream / non-file source: {cs.get('file')}")
        else:
            addline(stdscr, 3, 0, "No track playing. Start a song in your MPD client.")
        addline(stdscr, h - 2, 0, msg, curses.A_DIM)
        addline(stdscr, h - 1, 0, "[r] refresh   [q] quit", curses.A_DIM)
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == -1:
            time.sleep(0.1)
            continue
        if ch in (ord("q"), ord("Q")):
            return None
        if ch in (ord("r"), ord("R")):
            last_poll = 0.0
            msg = ""
            continue
        if not playable:
            msg = "No timeable track playing in MPD."
            continue

        if ch in (ord("p"), ord("P")) and not have_txt:
            content = run_editor(stdscr)
            if content.strip():
                txt_path.parent.mkdir(parents=True, exist_ok=True)
                if not content.endswith("\n"):
                    content += "\n"
                txt_path.write_text(content, encoding="utf-8")
                msg = f"Saved {rel_repo(txt_path)}"
            else:
                msg = "Editor was empty - nothing saved."
            last_poll = 0.0
        elif ch in (ord("e"), ord("E")) and have_txt:
            content = run_editor(stdscr, txt_path.read_text(encoding="utf-8"))
            if content.strip():
                if not content.endswith("\n"):
                    content += "\n"
                txt_path.write_text(content, encoding="utf-8")
                msg = f"Updated {rel_repo(txt_path)}"
            else:
                msg = "Edit left it empty - not saved."
            last_poll = 0.0
        elif ch in (ord("c"), ord("C"), ord("o"), ord("O")) and have_txt:
            stdscr.nodelay(False)
            msg = sync_screen(stdscr, mpd, rel)  # rel is pinned for the whole sync
            stdscr.nodelay(True)
            last_poll = 0.0


# -------------------------------------------------------------------------- glue
def wait_key(stdscr, message, keys):
    stdscr.nodelay(False)
    stdscr.erase()
    addline(stdscr, 0, 0, message)
    addline(stdscr, 2, 0, keys)
    stdscr.refresh()
    while True:
        ch = stdscr.getch()
        if ch in (ord("r"), ord("R")):
            return True
        if ch in (ord("q"), ord("Q")):
            return False


def run(stdscr):
    global C
    curses.curs_set(0)
    stdscr.keypad(True)
    C = init_colors()
    while True:
        try:
            mpd = MPD()
        except (OSError, MPDError) as exc:
            if not wait_key(stdscr, f"Cannot reach MPD: {exc}", "[r] retry   [q] quit"):
                return
            continue
        try:
            if main_screen(stdscr, mpd) is None:
                return
        except (OSError, MPDError) as exc:
            if not wait_key(stdscr, f"MPD connection lost: {exc}",
                            "[r] reconnect   [q] quit"):
                return
        finally:
            mpd.close()


def main():
    if not LYRICS_DIR.is_dir():
        sys.exit(f"lyrics dir not found: {LYRICS_DIR}")
    curses.wrapper(run)


if __name__ == "__main__":
    main()
