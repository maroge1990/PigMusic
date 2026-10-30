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

const KEY = "pig-music-listens";
const store = new Map();
globalThis.localStorage = {
  getItem: (key) => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => store.set(key, String(value)),
  removeItem: (key) => store.delete(key),
};
globalThis.window = { addEventListener() {}, dispatchEvent() {} };
globalThis.document = { hidden: true };
const originalInterval = globalThis.setInterval;
globalThis.setInterval = () => 0;
const { flushListens, recordListen } = await import("../src/listens.ts");
globalThis.setInterval = originalInterval;

const now = () => Math.floor(Date.now() / 1000);
const listen = (id, playedAt = now()) => ({
  event_id: id,
  track_id: "song",
  played_at: playedAt,
  seconds: 40,
});
// recordListen starts the send itself; let it finish.
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));
const stored = () => JSON.parse(store.get(KEY) || "[]").map((l) => l.event_id);

function serve(...statuses) {
  const sent = [];
  globalThis.fetch = async (path, options) => {
    sent.push(JSON.parse(options.body).events.map((l) => l.event_id));
    return new Response('{"recorded":0}', { status: statuses.shift() ?? 200 });
  };
  return sent;
}

test("a batch the server refuses is dropped, so later listens still reach it (#66)", async () => {
  store.clear();
  const sent = serve(400, 200);
  recordListen(listen("refused"));
  await settle();
  assert.deepEqual(stored(), []);
  recordListen(listen("next"));
  await settle();
  assert.deepEqual(sent, [["refused"], ["next"]]);
  assert.deepEqual(stored(), []);
});

test("signed-out, rate-limited and server failures keep listens for a retry", async () => {
  for (const status of [401, 429, 503]) {
    store.clear();
    serve(status);
    recordListen(listen("kept"));
    await settle();
    assert.deepEqual(stored(), ["kept"], `status ${status}`);
  }
});

test("listens older than the server's 30-day window are never sent", async () => {
  store.clear();
  store.set(
    KEY,
    JSON.stringify([listen("stale", now() - 31 * 86400), listen("fresh")]),
  );
  const sent = serve(200);
  await flushListens();
  assert.deepEqual(sent, [["fresh"]]);
  assert.deepEqual(stored(), []);
});
