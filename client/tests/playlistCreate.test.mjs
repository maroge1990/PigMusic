// #288: a playlist create whose answer is lost, or comes back as a gateway
// error, may have been saved. It is unconfirmed, and creating the same name
// again goes under the same key, so the server answers with the playlist
// it made instead of making a second.
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
    const error = await createPlaylist("Road trip").catch((e) => e);
    assert.ok(isUnconfirmed(error), String(error));
    const again = await createPlaylist("Road trip");
    assert.equal(again.id, "p1");
    assert.equal(state.made.size, 1);
    assert.equal(state.keys[1], state.keys[0]);
    // Confirmed: the next create of the name is a new playlist.
    assert.equal((await createPlaylist("Road trip")).id, "p2");
    assert.notEqual(state.keys[2], state.keys[0]);
  });

test("a refused create's next try is a new create; another name has its own key", async () => {
  clearSessionReads();
  setSessionId("session-1");
  const state = server();
  state.next = ["refuse", 504];
  await assert.rejects(createPlaylist("Road trip"), /Choose a playlist name/);
  await assert.rejects(createPlaylist("Road trip"), isUnconfirmed);
  assert.notEqual(state.keys[1], state.keys[0]);
  await createPlaylist("Gym");
  assert.notEqual(state.keys[2], state.keys[1]);
  // The unconfirmed one's key is still kept for its name.
  await createPlaylist("Road trip");
  assert.equal(state.keys[3], state.keys[1]);
});

test("the server's own busy answer stays a busy error", async () => {
  clearSessionReads();
  setSessionId("session-1");
  const state = server();
  state.next = ["busy"];
  const error = await createPlaylist("Road trip").catch((e) => e);
  assert.equal(error.kind, "busy");
});

test("a key is forgotten when the person signs out", async () => {
  clearSessionReads();
  setSessionId("session-1");
  const state = server();
  state.next = ["lose"];
  await assert.rejects(createPlaylist("Road trip"), isUnconfirmed);
  clearSessionReads();
  setSessionId("session-2");
  await createPlaylist("Road trip");
  assert.notEqual(state.keys[1], state.keys[0]);
});
