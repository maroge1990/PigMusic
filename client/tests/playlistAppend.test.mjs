// Codex (#249): a large playlist copy never doubles or skips songs: a
// refused batch can be tried again from where it stopped, and a batch whose
// answer was lost is never sent again on a guess.
import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = { addEventListener() {}, dispatchEvent() {} };
globalThis.Event = class {
  constructor(type) {
    this.type = type;
  }
};
const { setSessionId } = await import("../src/sessionId.ts");
const { MAX_SONGS_PER_REQUEST } = await import("../src/limits.ts");
const { ReportedFailure, addToPlaylist } = await import("../src/playlists.ts");
const { clearNotices, runAction, subscribe } = await import(
  "../src/notices.ts"
);

/** A playlist server: appends all or none, refuses an old revision. */
function playlistServer() {
  const server = {
    playlist: { name: "Gym", revision: 1, entries: [] },
    appends: 0,
    /** What the next append does: "refuse", "lose" or nothing. */
    next: null,
    /** The whole server is unreachable. */
    down: false,
  };
  globalThis.fetch = async (path, options) => {
    if (server.down) throw new TypeError("Failed to fetch");
    if ((options?.method || "GET") === "GET")
      return Response.json(server.playlist);
    server.appends += 1;
    const revision = Number(
      new URL(path, "http://x").searchParams.get("revision"),
    );
    if (revision !== server.playlist.revision)
      return Response.json(
        { detail: "Playlist changed.", code: "conflict" },
        { status: 409 },
      );
    const failure = server.next;
    server.next = null;
    if (failure === "refuse")
      return Response.json(
        { detail: "Couldn't save. Try again.", code: "request_failed" },
        { status: 400 },
      );
    server.playlist.entries.push(...JSON.parse(options.body).tracks);
    server.playlist.revision += 1;
    if (failure === "lose") throw new TypeError("Failed to fetch");
    // Saved, then the server or its gateway failed building the answer.
    if (failure === 500 || failure === 502)
      return new Response("error", { status: failure });
    return Response.json(server.playlist);
  };
  return server;
}

const songs = (n) =>
  Array.from({ length: n }, (_, i) => ({ title: "Song " + i, artist: "A" }));

function watch() {
  let all = [];
  const stop = subscribe((now) => (all = now));
  return { error: () => all.find((n) => n.kind === "error"), stop };
}

test("Try again resumes after a refusal, through an outage, to 501 songs", async () => {
  setSessionId("session-1");
  const server = playlistServer();
  const notices = watch();
  const copy = songs(MAX_SONGS_PER_REQUEST + 1);
  // The first batch goes in; the second is refused.
  const appendAfterFirst = globalThis.fetch;
  globalThis.fetch = async (path, options) => {
    if (options?.method === "POST" && server.appends === 1)
      server.next = "refuse";
    return appendAfterFirst(path, options);
  };
  await assert.rejects(addToPlaylist("p1", copy), ReportedFailure);
  assert.equal(server.playlist.entries.length, MAX_SONGS_PER_REQUEST);
  const first = notices.error();
  assert.match(first.text, /Added 500 of 501 songs to Gym/);

  // Try again while the server is out of reach: the same offer stays.
  server.down = true;
  await runAction(first.id);
  const second = notices.error();
  assert.equal(second.text, first.text);
  assert.equal(second.action.label, "Try again");
  assert.equal(server.playlist.entries.length, MAX_SONGS_PER_REQUEST);

  // Back again: Try again adds only the rest.
  server.down = false;
  await runAction(second.id);
  notices.stop();
  clearNotices();
  assert.equal(server.playlist.entries.length, MAX_SONGS_PER_REQUEST + 1);
  assert.deepEqual(
    server.playlist.entries.map((e) => e.title),
    copy.map((e) => e.title),
  );
});

test("a lost answer stops the copy and says so, without resending", async () => {
  setSessionId("session-1");
  const server = playlistServer();
  const notices = watch();
  server.next = "lose";
  await assert.rejects(addToPlaylist("p1", songs(3)), ReportedFailure);
  const error = notices.error();
  notices.stop();
  clearNotices();
  assert.equal(
    error.text,
    "Couldn't tell whether the last 3 songs were added to Gym. Check the playlist.",
  );
  assert.equal(error.action, undefined);
  assert.equal(server.appends, 1);
  assert.equal(server.playlist.entries.length, 3);
});

test("a playlist that moved is refused unmade and can be tried again", async () => {
  setSessionId("session-1");
  const server = playlistServer();
  const notices = watch();
  // Someone else edits between the read and the append.
  const original = globalThis.fetch;
  globalThis.fetch = async (path, options) => {
    if (options?.method === "POST" && server.appends === 0)
      server.playlist.revision += 1;
    return original(path, options);
  };
  await assert.rejects(addToPlaylist("p1", songs(2)), ReportedFailure);
  assert.equal(server.playlist.entries.length, 0);
  await runAction(notices.error().id);
  notices.stop();
  clearNotices();
  assert.equal(server.playlist.entries.length, 2);
});

for (const status of [500, 502])
  test(`a ${status} after the batch was saved stops without resending`, async () => {
    setSessionId("session-1");
    const server = playlistServer();
    const notices = watch();
    server.next = status;
    await assert.rejects(addToPlaylist("p1", songs(2)), ReportedFailure);
    const error = notices.error();
    notices.stop();
    clearNotices();
    assert.equal(
      error.text,
      "Couldn't tell whether the last 2 songs were added to Gym. Check the playlist.",
    );
    assert.equal(error.action, undefined);
    assert.equal(server.appends, 1);
    assert.equal(server.playlist.entries.length, 2);
  });
