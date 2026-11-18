import assert from "node:assert/strict";
import test from "node:test";
import { registerHooks } from "node:module";
registerHooks({
  resolve(specifier, context, next) {
    try {
      return next(specifier, context);
    } catch (error) {
      if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier))
        return next(specifier + ".ts", context);
      throw error;
    }
  },
});
globalThis.window = { addEventListener() {}, dispatchEvent() {} };
globalThis.document = { hidden: true };
const interval = globalThis.setInterval;
globalThis.setInterval = () => 0;
const { playlistSongs, fromTrack, songBody, goToArtist, withLibrary } =
  await import("../src/songs.ts");
globalThis.setInterval = interval;

test("album requests normalise numeric years and retain release context", () => {
  const track = {
    id: "track",
    title: "Live song",
    artist: "Artist",
    album: "Live album",
    year: 2020,
    musicbrainz_release_id: "edition",
    musicbrainz_recording_id: "recording",
    duration: 241,
    disc_number: 2,
    track_number: 4,
  };
  const body = songBody(fromTrack(track));
  assert.equal(body.year, "2020");
  assert.equal(body.musicbrainz_release_id, "edition");
  assert.equal(body.musicbrainz_recording_id, "recording");
  assert.equal(body.disc_number, 2);
});

test("playlist queues retain cloud entries, duplicate positions and saved file identity", () => {
  const saved = {
    id: "file",
    title: "Saved",
    artist: "Artist",
    server_state: "in_library",
  };
  const cloud = {
    id: "imported",
    title: "Cloud",
    artist: "Artist",
    album: "Album",
    server_state: "not_in_library",
    available: true,
  };
  const entries = [
    { ...saved, entry_id: "list:0", saved_track: saved },
    { ...cloud, entry_id: "list:1" },
    { ...cloud, entry_id: "list:2" },
  ];
  const queue = playlistSongs(entries);
  assert.deepEqual(
    queue.map((s) => s.key),
    ["list:0", "list:1", "list:2"],
  );
  assert.deepEqual(
    queue.map((s) => s.state),
    ["saved", "missing", "missing"],
  );
  assert.equal(queue[0].saved.id, "file");
  assert.equal(queue[1].saved, undefined);
  assert.equal(songBody(queue[1]).album, "Album");
});

test("the library lookup asks about every song, 200 at a time, with its album, and tries a part again (#263)", async () => {
  const songs = Array.from({ length: 450 }, (_, i) => ({
    key: "k" + i,
    title: "Song " + i,
    artist: "Artist",
    album: "Live at Leeds",
    state: "missing",
  }));
  const { setSessionId } = await import("../src/sessionId.ts");
  setSessionId("session-1");
  const asked = [];
  let failed = false;
  globalThis.fetch = async (_path, options) => {
    const body = JSON.parse(options.body);
    if (!failed) {
      failed = true;
      return new Response('{"detail":"busy"}', { status: 500 });
    }
    asked.push(body.songs);
    return new Response(
      JSON.stringify({
        tracks: body.songs.map((s) =>
          s.title === "Song 449" ? { id: "mine", title: s.title } : null,
        ),
      }),
      { headers: { "Content-Type": "application/json" } },
    );
  };
  const found = await withLibrary(songs);
  assert.deepEqual(
    asked.map((part) => part.length),
    [200, 200, 50],
  );
  assert.equal(asked[0][0].album, "Live at Leeds");
  assert.equal(found[449].state, "saved");
  assert.equal(found[448].state, "missing");
});

test("a catalogue artist ID opens the full artist page without consulting library membership", async () => {
  globalThis.location = { hash: "" };
  globalThis.fetch = () => {
    throw new Error("Should not read the library");
  };
  await goToArtist("Madonna", "catalogue-id");
  assert.match(location.hash, /search\/artist\/catalogue-id/);
});
