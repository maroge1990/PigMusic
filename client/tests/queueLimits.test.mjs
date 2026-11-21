// D-128, #242: past a queue limit the server queues the songs that fit and
// says how many it left out; an Add says so, and offers to send those again.
import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = { addEventListener() {}, dispatchEvent() {} };
globalThis.Event = class {
  constructor(type) {
    this.type = type;
  }
};
const { setSessionId } = await import("../src/sessionId.ts");
const { api } = await import("../src/api.ts");
const { addSongsToLibrary } = await import("../src/songActions.ts");
const { leftOutMessage } = await import("../src/queueLimits.ts");
const { clearNotices, runAction, subscribe } = await import(
  "../src/notices.ts"
);

const song = (i) => ({
  key: "k" + i,
  title: "Song " + i,
  artist: "Artist",
  album: "Album",
  state: "missing",
});

test("each limit has its sentence", () => {
  assert.equal(leftOutMessage({ left_out: 0, limit: null }), "");
  assert.equal(
    leftOutMessage({ left_out: 3, limit: "person_waiting" }),
    "Couldn't add 3 songs: you already have 2,000 songs waiting. Try again once some are added.",
  );
  assert.equal(
    leftOutMessage({ left_out: 1, limit: "household_waiting" }),
    "Couldn't add 1 song: the server already has 10,000 songs waiting. Try again once some are added.",
  );
  assert.equal(
    leftOutMessage({ left_out: 2, limit: "rate", retry_after: 61 }),
    "Couldn't add 2 songs: that's over 1,000 songs in 10 minutes. Try again in 2 minutes.",
  );
  assert.equal(
    leftOutMessage({ left_out: 2, limit: "rate", retry_after: 5 }),
    "Couldn't add 2 songs: that's over 1,000 songs in 10 minutes. Try again in 1 minute.",
  );
});

test("a busy answer for songs all past a limit says which", async () => {
  setSessionId("session-1");
  globalThis.fetch = async () =>
    Response.json(
      {
        detail: "Too many songs are waiting to be added. Try again later.",
        code: "queue_limit",
        left_out: 1,
        limit: "person_waiting",
        retry_after: null,
        outcomes: ["over_limit"],
      },
      { status: 429 },
    );
  await assert.rejects(
    api("/v1/requests", { method: "POST", body: "{}" }),
    (error) =>
      error.code === "queue_limit" &&
      error.message ===
        "Couldn't add 1 song: you already have 2,000 songs waiting. Try again once some are added.",
  );
});

test("an Add past a limit says what was added and what wasn't, and can send those again", async () => {
  setSessionId("session-1");
  const sent = [];
  let full = true;
  globalThis.fetch = async (path, options) => {
    const titles = JSON.parse(options.body).tracks.map((t) => t.title);
    sent.push(titles);
    if (full)
      return Response.json({
        created: 1,
        existing: 0,
        skipped: 0,
        left_out: 2,
        limit: "rate",
        retry_after: 30,
        outcomes: ["created", "over_limit", "over_limit"],
      });
    return Response.json({
      created: titles.length,
      existing: 0,
      skipped: 0,
      outcomes: titles.map(() => "created"),
    });
  };
  let shown = [];
  const stop = subscribe((all) => (shown = all));
  await addSongsToLibrary([song(1), song(2), song(3)]);
  const error = shown.find((n) => n.kind === "error");
  assert.equal(
    error.text,
    "Adding 1 song to your library. Couldn't add 2 songs: that's over 1,000 songs in 10 minutes. Try again in 1 minute.",
  );
  assert.equal(error.action.label, "Try again");
  // Try again sends only the songs left out.
  full = false;
  await runAction(error.id);
  stop();
  assert.deepEqual(sent, [
    ["Song 1", "Song 2", "Song 3"],
    ["Song 2", "Song 3"],
  ]);
  assert.equal(shown.at(-1).text, "Adding 3 songs to your library.");
  clearNotices();
});
