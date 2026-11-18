// Codex (#249): a large playlist copy never doubles the songs already in,
// whether a batch failed or its answer was lost.
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
function playlistServer({ fail = () => null } = {}) {
  const playlist = { revision: 1, entries: [] };
  let appends = 0;
  globalThis.fetch = async (path, options) => {
    if ((options?.method || "GET") === "GET") return Response.json(playlist);
    const url = new URL(path, "http://x");
    const revision = Number(url.searchParams.get("revision"));
    if (revision !== playlist.revision)
      return Response.json(
        {
          detail: "Playlist changed. Refresh before editing.",
          code: "conflict",
        },
        { status: 409 },
      );
    const tracks = JSON.parse(options.body).tracks;
    const failure = fail(++appends, tracks);
    if (failure === "refuse")
      return Response.json(
        { detail: "Server error", code: "server_error" },
        { status: 500 },
      );
    playlist.entries.push(...tracks);
    playlist.revision += 1;
    // Saved, but the answer never arrives.
    if (failure === "lose") throw new TypeError("Failed to fetch");
    return Response.json(playlist);
  };
  return playlist;
}

const songs = (n) =>
  Array.from({ length: n }, (_, i) => ({ title: "Song " + i, artist: "A" }));

test("Try again after the last batch failed adds only the rest", async () => {
  setSessionId("session-1");
  let refuse = true;
  const playlist = playlistServer({
    fail: (n) => (n === 2 && refuse ? ((refuse = false), "refuse") : null),
  });
  let shown = [];
  const stop = subscribe((all) => (shown = all));
  const copy = songs(MAX_SONGS_PER_REQUEST + 1);
  await assert.rejects(addToPlaylist("p1", copy), ReportedFailure);
  assert.equal(playlist.entries.length, MAX_SONGS_PER_REQUEST);
  const error = shown.find((n) => n.kind === "error");
  assert.match(error.text, /Added 500 of 501 songs/);
  await runAction(error.id);
  stop();
  clearNotices();
  assert.equal(playlist.entries.length, MAX_SONGS_PER_REQUEST + 1);
  assert.deepEqual(
    playlist.entries.map((e) => e.title),
    copy.map((e) => e.title),
  );
});

test("a batch saved whose answer was lost is not sent again", async () => {
  setSessionId("session-1");
  const playlist = playlistServer({ fail: (n) => (n === 1 ? "lose" : null) });
  const copy = songs(3);
  await addToPlaylist("p1", copy);
  assert.deepEqual(
    playlist.entries.map((e) => e.title),
    copy.map((e) => e.title),
  );
  assert.equal(playlist.revision, 2);
});
