#!/usr/bin/env osascript -l JavaScript
/*
 * fetch.js - dump every Apple Music.app user playlist as JSON.
 *
 * stdout (default): one JSON array
 *   [{ "name", "special", "smart", "tracks": ["<abs posix path>", ...] }, ...]
 * stdout (--ndjson): one JSON object per line, flushed as each playlist is
 *   read - pipe it through `pv -l` for a progress bar, or into
 *   `sync.py --from -`.
 * stderr (always): a running progress line per playlist, e.g.
 *   [ 47/213] Rock Hindi  (52 tracks)
 *
 * Folder playlists and the built-in Library/Music views are skipped; cloud-only
 * tracks (no local file) are dropped. Read-only.
 */
ObjC.import("Foundation");

function writeOut(s) {
  $.NSFileHandle.fileHandleWithStandardOutput.writeData(
    $.NSString.alloc.initWithUTF8String(s).dataUsingEncoding($.NSUTF8StringEncoding));
}
function progress(s) {
  $.NSFileHandle.fileHandleWithStandardError.writeData(
    $.NSString.alloc.initWithUTF8String(s).dataUsingEncoding($.NSUTF8StringEncoding));
}
function pad(n, w) { var s = String(n); while (s.length < w) s = " " + s; return s; }

function run(argv) {
  argv = argv || [];
  var namesOnly = argv.indexOf("--names") !== -1;
  var oneIndex = argv.indexOf("--one");
  var selected = oneIndex === -1 ? null : argv[oneIndex + 1];
  var ndjson = (argv || []).indexOf("--ndjson") !== -1;
  var Music = Application("Music");
  var pls = Music.userPlaylists;
  var names = pls.name();
  var total = names.length;
  var out = [];

  for (var i = 0; i < total; i++) {
    var pl = pls[i];
    var name = names[i];
    if (selected !== null && name !== selected) continue;

    var special;
    try { special = pl.specialKind(); } catch (e) { special = "none"; }
    var skip = (special === "Folder") ||
               (special && special !== "none" && special !== "Purchased Music");

    var smart = false;
    if (namesOnly) {
      if (!skip) out.push({name: name, tracks: []});
      continue;
    }
    try { smart = pl.smart(); } catch (e) {}

    var paths = [];
    if (!skip) {
      var locs = null;
      try { locs = pl.tracks.location(); } catch (e) { locs = null; }
      if (locs === null) {
        var ts = pl.tracks, n = 0;
        try { n = ts.length; } catch (e) { n = 0; }
        for (var k = 0; k < n; k++) {
          try { var l = ts[k].location(); if (l) paths.push(l.toString()); }
          catch (e2) {}
        }
      } else {
        for (var j = 0; j < locs.length; j++) if (locs[j]) paths.push(locs[j].toString());
      }
    }

    progress("[" + pad(i + 1, String(total).length) + "/" + total + "] " + name +
             (skip ? "  (skipped)" : "  (" + paths.length + " tracks)") + "\n");

    if (skip) continue;
    var rec = { name: name, special: special || "none", smart: smart, tracks: paths };
    if (ndjson) writeOut(JSON.stringify(rec) + "\n");
    else out.push(rec);
  }

  return ndjson ? "" : JSON.stringify(out);
}
