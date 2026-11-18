// #248, #249, #250: the writes the song and collection menus make go through
// api(), so each names the session it was started under; Remove from
// library's Undo keeps the session that removed the songs.
import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = { addEventListener() {}, dispatchEvent() {} };
globalThis.Event = class {
  constructor(type) {
    this.type = type;
  }
};
const { setSessionId } = await import("../src/sessionId.ts");
const { addSongsToLibrary, removeFromLibrary } = await import(
  "../src/songActions.ts"
);
const { addToPlaylist } = await import("../src/playlists.ts");
const { clearNotices, runAction, subscribe } = await import(
  "../src/notices.ts"
);

function serve(answers = {}) {
  const sent = [];
  globalThis.fetch = async (path, options) => {
    sent.push([
      options?.method || "GET",
      path,
      new Headers(options?.headers).get("X-Pig-Expected-Session"),
    ]);
    return Response.json(answers[path] ?? {});
  };
  return sent;
}

const song = {
  key: "k1",
  title: "Von dutch",
  artist: "Charli xcx",
  album: "Brat",
  state: "missing",
};

test("collection menu writes name the session", async () => {
  setSessionId("session-1");
  const sent = serve({
    "/api/v1/requests": { created: 1, existing: 0, skipped: 0 },
  });
  await addSongsToLibrary([song, { ...song, key: "k2", title: "360" }]);
  await addToPlaylist("p1", [{ title: "Von dutch" }, { title: "360" }]);
  assert.deepEqual(sent, [
    ["POST", "/api/v1/requests", "session-1"],
    ["POST", "/api/v1/playlists/p1/songs?allow_repeat=true", "session-1"],
    ["POST", "/api/v1/playlists/p1/songs?allow_repeat=true", "session-1"],
  ]);
  clearNotices();
});

test("Remove from library and its Undo name the session that removed", async () => {
  setSessionId("session-1");
  const sent = serve({
    "/api/v1/library/removal-preview": { confirmation: "c", items: [] },
    "/api/v1/library/remove": {
      id: "r1",
      undo_until: Date.now() / 1000 + 60,
    },
  });
  let shown = [];
  const stop = subscribe((all) => (shown = all));
  await removeFromLibrary(["t1"]);
  const undo = shown.find((n) => n.action?.label === "Undo");
  assert.ok(undo, "Undo is offered");
  // Another sign-in happens before Undo is pressed: the Undo still names
  // the session it belongs to, so the server can refuse it.
  setSessionId("session-2");
  await runAction(undo.id);
  stop();
  clearNotices();
  assert.deepEqual(sent, [
    ["POST", "/api/v1/library/removal-preview", "session-1"],
    ["POST", "/api/v1/library/remove", "session-1"],
    ["POST", "/api/v1/library/removals/r1/undo", "session-1"],
  ]);
});
