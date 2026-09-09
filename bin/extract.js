#!/usr/bin/env osascript -l JavaScript
/*
 * extract.js - dump a snapshot of Apple Music.app play stats as CSV to stdout.
 *
 * Emits one row per track in the main library (cloud / Apple Music entries
 * included - those have an empty path_abs). Read-only: never writes to Music.
 *
 *   osascript -l JavaScript play-stats/extract.js > snapshot.csv
 *
 * Normally you don't call this directly - sync.py runs it and merges the
 * snapshot into play_stats.csv (see README.md).
 *
 * Columns:
 *   persistent_id  Music.app persistent ID - stable across renames/moves, the
 *                  merge key. Never reused.
 *   path_abs       absolute POSIX path of the media file ("" for cloud tracks)
 *   path_lib       path_abs with the music-root prefix stripped, i.e. the same
 *                  relative path MPD's music_directory uses on this Mac
 *   path_slug      BEST-EFFORT lowercase/hyphen slug of path_lib (extension
 *                  kept). A hint for grep, not a key.
 *   join_key       path_slug with the extension and the leading track-number
 *                  dropped from the last segment:
 *                    a-r-rahman/dil-se/satrangi-re
 *                  This is the most reliable column for joining to lyrics/ and
 *                  playlists/ - strip a repo file's own leading number and its
 *                  -[mid-####] suffix and compare. Still imperfect: featured
 *                  artists, "?"/"!" in album names and (feat. ...) parts can
 *                  differ. ~85% of tracks that have a repo lyrics file join.
 *   artist, album, title
 *   track_number, disc_number
 *   rating         0-100 (20 per star)
 *   rating_kind    "user" (really rated) | "computed" (inherited from album -
 *                  treat as unrated)
 *   play_count
 *   skip_count
 *   played_date    ISO-8601, or "" if never
 *   cloud_status
 */

// Media roots on this Mac. The first matching prefix is stripped from
// path_abs to get path_lib (the "Music/" one matches ~/.config/mpd/mpd.conf's
// music_directory). Purchased/downloaded Apple Music tracks live under
// "Apple Music/" as DRM .m4p and have no real repo counterpart.
// Anything under none of these keeps its full absolute path in path_lib.
var MUSIC_ROOTS = [
  "/Users/susamn/Music/Music/Media.localized/Music/",
  "/Users/susamn/Music/Music/Media.localized/Apple Music/"
];

function csvField(v) {
  var s = (v === null || v === undefined) ? "" : String(v);
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function slugSegment(seg) {
  return seg
    .normalize("NFKD").replace(/[̀-ͯ]/g, "")   // strip diacritics
    .toLowerCase()
    .replace(/\s*&\s*/g, "&")
    .replace(/\s*@\s*/g, "@")
    .replace(/,\s+/g, ",")
    .replace(/_/g, "")            // Apple stores ":" in names as "_" on disk
    .replace(/\./g, "")           // "A. R. Rahman" -> "a-r-rahman"
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

function slugPath(relPath) {
  if (!relPath) return "";
  var dot = relPath.lastIndexOf(".");
  var ext = dot > relPath.lastIndexOf("/") ? relPath.slice(dot).toLowerCase() : "";
  var body = ext ? relPath.slice(0, dot) : relPath;
  return body.split("/").map(slugSegment).join("/") + ext;
}

function joinKey(slug) {
  if (!slug) return "";
  var dot = slug.lastIndexOf(".");
  var body = dot > slug.lastIndexOf("/") ? slug.slice(0, dot) : slug;
  var cut = body.lastIndexOf("/") + 1;
  var last = body.slice(cut).replace(/^\d+(-\d+)?-/, "");
  return body.slice(0, cut) + last;
}

function isoDate(d) {
  if (!d) return "";
  try { return d.toISOString(); } catch (e) { return ""; }
}

function run() {
  var Music = Application("Music");
  var lib = Music.libraryPlaylists[0];

  // location() cannot be fetched in bulk over the whole library (cloud tracks
  // throw), but it works over fileTracks. Build id -> path from there.
  var ft = lib.fileTracks;
  var ftIds = ft.persistentID();
  var ftLoc = ft.location();
  var pathById = {};
  for (var i = 0; i < ftIds.length; i++) {
    pathById[ftIds[i]] = ftLoc[i] ? ftLoc[i].toString() : "";
  }

  var t = lib.tracks;
  var id = t.persistentID();
  var name = t.name();
  var artist = t.artist();
  var album = t.album();
  var trackNo = t.trackNumber();
  var discNo = t.discNumber();
  var rating = t.rating();
  var ratingKind = t.ratingKind();
  var playCount = t.playedCount();
  var skipCount = t.skippedCount();
  var playedDate = t.playedDate();
  var cloudStatus = t.cloudStatus();

  var cols = ["persistent_id", "path_abs", "path_lib", "path_slug", "join_key",
    "artist", "album", "title", "track_number", "disc_number", "rating",
    "rating_kind", "play_count", "skip_count", "played_date", "cloud_status"];
  var out = [cols.join(",")];

  for (var j = 0; j < id.length; j++) {
    var abs = pathById[id[j]] || "";
    var libRel = abs;
    for (var r = 0; r < MUSIC_ROOTS.length; r++) {
      if (abs && abs.indexOf(MUSIC_ROOTS[r]) === 0) {
        libRel = abs.slice(MUSIC_ROOTS[r].length);
        break;
      }
    }
    var slug = slugPath(libRel);
    var row = [
      id[j], abs, libRel, slug, joinKey(slug),
      artist[j], album[j], name[j],
      trackNo[j] || "", discNo[j] || "",
      rating[j], ratingKind[j], playCount[j], skipCount[j],
      isoDate(playedDate[j]), cloudStatus[j] || ""
    ];
    out.push(row.map(csvField).join(","));
  }

  return out.join("\n") + "\n";
}
