// #288: a playlist create whose answer is lost, or comes back as a gateway
// error, may have been saved. It is unconfirmed, and that attempt's Try
// again goes under the same key, so the server answers with the playlist it
// made instead of making a second. A fresh create always has a new key.
import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = { addEventListener() {}, dispatchEvent() {} };
globalThis.Event = class {
  constructor(type) {
    this.type = type;
  }
};
const { setSessionId } = await import("../src/sessionId.ts");
const { clearSessionReads, isUnconfirmed } = await import("../src/api.ts");
const { createPlaylist } = await import("../src/playlists.ts");

/** A server that makes one playlist per key; `next` is how it answers. */
function server() {
  const state = { made: new Map(), keys: [], next: [] };
  globalThis.fetch = async (_path, options) => {
    const { name, operation_id } = JSON.parse(options.body);
    state.keys.push(operation_id);
    const answer = state.next.shift();
    if (answer === "refuse")
      return Response.json(
        { detail: "Choose a playlist name.", code: "x" },
        { status: 400 },
      );
    if (answer === "busy")
      return Response.json(
        { detail: "The server is busy.", code: "server_busy" },
        { status: 503 },
      );
    const playlist = state.made.get(operation_id) ?? {
      id: "p" + (state.made.size + 1),
      name,
    };
    state.made.set(operation_id, playlist);
    if (answer === "lose") throw new TypeError("Failed to fetch");
    if (typeof answer === "number")
      return new Response("<html>gateway</html>", { status: answer });
    return Response.json(playlist);
  };
  return state;
}

for (const answer of [502, 503, 504, "lose"])
  test(`a create answered ${answer} is unconfirmed and its retry makes no second`, async () => {
    clearSessionReads();
    setSessionId("session-1");
    const state = server();
    state.next = [answer];
    const error = await createPlaylist("Road trip", "key-1").catch((e) => e);
    assert.ok(isUnconfirmed(error), String(error));
    // That attempt's Try again: the playlist it made.
    const again = await createPlaylist("Road trip", "key-1");
    assert.equal(again.id, "p1");
    assert.equal(state.made.size, 1);
    assert.deepEqual(state.keys, ["key-1", "key-1"]);
  });

// Codex round 1: keys were kept per name, so a fresh New playlist of the
// same name after an unconfirmed one got that earlier playlist (renamed
// since, perhaps), and the picker added songs to it.
test("every fresh create has its own key, whatever its name", async () => {
  clearSessionReads();
  setSessionId("session-1");
  const state = server();
  state.next = [504];
  await assert.rejects(createPlaylist("Road trip"), isUnconfirmed);
  const fresh = await createPlaylist("Road trip");
  assert.equal(fresh.id, "p2");
  assert.notEqual(state.keys[1], state.keys[0]);
  assert.match(state.keys[0], /^[0-9a-f-]{36}$/);
});

test("a refusal and the server's own busy answer stay as they were", async () => {
  clearSessionReads();
  setSessionId("session-1");
  const state = server();
  state.next = ["refuse", "busy"];
  await assert.rejects(createPlaylist("Road trip"), /Choose a playlist name/);
  const error = await createPlaylist("Road trip").catch((e) => e);
  assert.equal(error.kind, "busy");
});
