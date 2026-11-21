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
        outcomes: ["excluded"],
        over_limit: [0],
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
        // As installed iPhones can read it: excluded, and the positions a
        // limit left out.
        outcomes: ["created", "excluded", "excluded"],
        over_limit: [1, 2],
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

test("a later batch that fails keeps the songs an earlier limit left out", async () => {
  // Codex round 1: Try again sent only the failed batch, and the notice
  // counted one song instead of two.
  setSessionId("session-1");
  const { MAX_SONGS_PER_REQUEST } = await import("../src/limits.ts");
  const many = Array.from({ length: MAX_SONGS_PER_REQUEST + 1 }, (_, i) =>
    song(i),
  );
  const sent = [];
  globalThis.fetch = async (path, options) => {
    const titles = JSON.parse(options.body).tracks.map((t) => t.title);
    sent.push(titles);
    if (sent.length === 1)
      return Response.json({
        created: titles.length - 1,
        existing: 0,
        skipped: 0,
        left_out: 1,
        over_limit: [0],
        limit: "rate",
        retry_after: 30,
        outcomes: titles.map((_, i) => (i ? "created" : "excluded")),
      });
    if (sent.length === 2)
      return Response.json({ detail: "No", code: "nope" }, { status: 400 });
    return Response.json({ created: titles.length, existing: 0, skipped: 0 });
  };
  let shown = [];
  const stop = subscribe((all) => (shown = all));
  await addSongsToLibrary(many);
  const error = shown.find((n) => n.kind === "error");
  const max = MAX_SONGS_PER_REQUEST;
  assert.equal(
    error.text,
    `Adding ${max - 1} songs to your library. Couldn't add the other 2 songs. Try again.`,
  );
  await runAction(error.id);
  stop();
  assert.deepEqual(sent[2], ["Song 0", `Song ${max}`]);
  clearNotices();
});

test("past a waiting limit the later batches are still sent", async () => {
  // Codex round 2: one refused batch stopped the run, though a later song
  // could still join a job already queued.
  setSessionId("session-1");
  const { MAX_SONGS_PER_REQUEST } = await import("../src/limits.ts");
  const max = MAX_SONGS_PER_REQUEST;
  const many = Array.from({ length: max + 2 }, (_, i) => song(i));
  const sent = [];
  globalThis.fetch = async (path, options) => {
    const titles = JSON.parse(options.body).tracks.map((t) => t.title);
    sent.push(titles);
    if (sent.length === 1)
      return Response.json(
        {
          detail: "Too many songs are waiting to be added. Try again later.",
          code: "queue_limit",
          limit: "household_waiting",
          left_out: titles.length,
          over_limit: titles.map((_, i) => i),
          outcomes: titles.map(() => "excluded"),
        },
        { status: 429 },
      );
    return Response.json({
      created: 0,
      existing: 1,
      skipped: 0,
      left_out: 1,
      over_limit: [1],
      limit: "household_waiting",
      outcomes: ["existing", "excluded"],
    });
  };
  let shown = [];
  const stop = subscribe((all) => (shown = all));
  await addSongsToLibrary(many);
  stop();
  assert.equal(sent.length, 2);
  const error = shown.find((n) => n.kind === "error");
  assert.equal(
    error.text,
    `Adding 1 song to your library. Couldn't add ${max + 1} songs: the server already has 10,000 songs waiting. Try again once some are added.`,
  );
  clearNotices();
});

test("a quality choice says how many replacements wait", async () => {
  const { qualityDeferredMessage, bulkLeftOutMessage } = await import(
    "../src/queueLimits.ts"
  );
  assert.equal(qualityDeferredMessage(0), "");
  assert.equal(
    qualityDeferredMessage(3),
    "3 songs will change quality later: too many songs are waiting. Choose this quality again once some are added.",
  );
  assert.equal(
    bulkLeftOutMessage(2, "Submit the import again"),
    "2 songs left out: too many songs are waiting. Submit the import again once some are added.",
  );
});

test("a Retry a waiting limit refused says why", async () => {
  const { retryLimitMessage } = await import("../src/queueLimits.ts");
  assert.equal(
    retryLimitMessage({ limit: "household_waiting" }),
    "Couldn't try again yet: the server already has 10,000 songs waiting. Try again once some are added.",
  );
});
