#!/usr/bin/env osascript -l JavaScript
/*
 * writeback.js - add tracks to a named Apple Music.app playlist by absolute
 * file path. The only Music.app *write* in this repo - see
 * docs/mac-playlist-writeback.md. Driven by bin/playlist-writeback.py, which
 * decides *what* to add (via MCATALOGID matching); this script only knows
 * "add these paths to this playlist."
 *
 * Two modes:
 *
 *   osascript -l JavaScript writeback.js --read "Playlist Name"
 *     Read-only. Prints {"found": true, "locations": [...]} if the playlist
 *     exists (track locations, in playlist order; cloud-only tracks with no
 *     local file are skipped, same as fetch.js), or {"found": false}.
 *
 *   osascript -l JavaScript writeback.js <path-to-request.json>
 *     request.json: {"playlist": "Name", "create_if_missing": true,
 *                    "add": ["/abs/path1.mp3", ...]}
 *     Finds or creates the playlist, re-reads its current locations as a
 *     final idempotency check (never double-adds), and for every requested
 *     path not already present: finds the matching library track by
 *     location and calls Music.duplicate(track, {to: playlist}). Prints
 *     {"created": bool, "added": [...], "already_present": [...],
 *      "not_in_library": [...]}.
 *
 * "not_in_library": the path isn't any track's location anywhere in the
 * whole Music library - it was never imported into Apple Music, so there is
 * nothing to duplicate into the playlist. Reported, never guessed at.
 */
ObjC.import("Foundation");

function readFile(path) {
  var data = $.NSData.dataWithContentsOfFile(path);
  if (!data) return null;
  var str = $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding);
  return str.js;
}

function writeOut(s) {
  $.NSFileHandle.fileHandleWithStandardOutput.writeData(
    $.NSString.alloc.initWithUTF8String(s).dataUsingEncoding($.NSUTF8StringEncoding));
}

// Same fallback fetch.js uses: bulk .location() throws if any track in the
// set is cloud-only, so fall back to a per-track try/catch pass.
function trackLocations(tracks) {
  var paths = [];
  var locs = null;
  try { locs = tracks.location(); } catch (e) { locs = null; }
  if (locs !== null) {
    for (var i = 0; i < locs.length; i++) if (locs[i]) paths.push(locs[i].toString());
    return paths;
  }
  var n = 0;
  try { n = tracks.length; } catch (e) { n = 0; }
  for (var k = 0; k < n; k++) {
    try { var l = tracks[k].location(); if (l) paths.push(l.toString()); }
    catch (e2) {}
  }
  return paths;
}

function findPlaylist(Music, name) {
  var pls = Music.userPlaylists;
  var names = pls.name();
  if (names.filter(function (n) { return n === name; }).length > 1) {
    throw new Error("Ambiguous playlist name: " + name);
  }
  for (var i = 0; i < names.length; i++) {
    if (names[i] === name) {
      var pl = pls[i];
      // Re-confirm the reference actually names what we just matched, not a
      // stale/misaligned one - seen once in testing (transient, root cause
      // unconfirmed) where an indexed pls[i] briefly returned a different
      // playlist's data than the same index's own name() had just reported.
      // Cheap, and this is the one check standing between "found the right
      // playlist" and writing into the wrong one.
      if (pl.name() !== name) {
        throw new Error("playlist reference mismatch: expected " + name +
          ", got " + pl.name());
      }
      return pl;
    }
  }
  return null;
}

function doRead(Music, name) {
  var pl = findPlaylist(Music, name);
  if (!pl) return JSON.stringify({found: false});
  return JSON.stringify({found: true, locations: trackLocations(pl.tracks)});
}

function doApply(Music, requestPath) {
  var raw = readFile(requestPath);
  if (!raw) return JSON.stringify({error: "cannot read " + requestPath});
  var req = JSON.parse(raw);

  var created = false;
  var pl = findPlaylist(Music, req.playlist);
  if (!pl && req.create_if_missing) {
    pl = Music.make({new: "playlist", withProperties: {name: req.playlist}});
    created = true;
  }
  if (!pl) return JSON.stringify({error: "playlist not found: " + req.playlist});

  var already = {};
  trackLocations(pl.tracks).forEach(function (p) { already[p] = true; });

  // location -> library file-track reference, for duplicate(). Built once,
  // same fileTracks-scoping trick extract.js uses (bulk location() throws
  // across the whole library otherwise - fileTracks alone doesn't).
  var lib = Music.libraryPlaylists[0];
  var ft = lib.fileTracks;
  var ftLoc = ft.location();
  var indexByLoc = {};
  for (var i = 0; i < ftLoc.length; i++) {
    if (ftLoc[i]) indexByLoc[ftLoc[i].toString()] = i;
  }

  var added = [], alreadyPresent = [], notInLibrary = [];
  var toAdd = req.add || [];
  for (var j = 0; j < toAdd.length; j++) {
    var path = toAdd[j];
    if (already[path]) { alreadyPresent.push(path); continue; }
    var idx = indexByLoc[path];
    if (idx === undefined) { notInLibrary.push(path); continue; }
    Music.duplicate(ft[idx], {to: pl});
    already[path] = true;
    added.push(path);
  }

  return JSON.stringify({
    created: created, added: added,
    already_present: alreadyPresent, not_in_library: notInLibrary
  });
}

function run(argv) {
  var Music = Application("Music");
  Music.includeStandardAdditions = true;

  if (argv[0] === "--library") {
    return JSON.stringify({locations: trackLocations(Music.libraryPlaylists[0].fileTracks)});
  }
  if (argv[0] === "--read") {
    return doRead(Music, argv[1]);
  }
  return doApply(Music, argv[0]);
}
