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
const { addSongsToLibrary, removeFromLibrary, withCollection } = await import(
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
    "/api/v1/playlists/p1": { revision: 4, entries: [] },
    "/api/v1/playlists/p1/songs?allow_repeat=true&revision=4": {
      revision: 5,
      entries: [],
    },
  });
  await addSongsToLibrary([song, { ...song, key: "k2", title: "360" }]);
  await addToPlaylist("p1", [{ title: "Von dutch" }, { title: "360" }]);
  assert.deepEqual(sent, [
    ["POST", "/api/v1/requests", "session-1"],
    ["GET", "/api/v1/playlists/p1", null],
    // Codex (#249): one atomic append at the revision read, not a request
    // per song.
    [
      "POST",
      "/api/v1/playlists/p1/songs?allow_repeat=true&revision=4",
      "session-1",
    ],
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

test("a sign-in during the preview never sends the removal as the new account", async () => {
  setSessionId("session-1");
  const sent = [];
  globalThis.fetch = async (path, options) => {
    sent.push([
      path,
      new Headers(options?.headers).get("X-Pig-Expected-Session"),
    ]);
    if (path === "/api/v1/library/removal-preview") {
      // Another account signs in while the preview is on its way.
      setSessionId("session-2");
      return Response.json({ confirmation: "c", items: [] });
    }
    return Response.json({ id: "r2", undo_until: Date.now() / 1000 + 60 });
  };
  await removeFromLibrary(["t1"]);
  clearNotices();
  assert.deepEqual(sent, [
    ["/api/v1/library/removal-preview", "session-1"],
    ["/api/v1/library/remove", "session-1"],
  ]);
});

test("a collection over the server's limit goes in batches; a failed one can be tried again", async () => {
  setSessionId("session-1");
  const { MAX_SONGS_PER_REQUEST } = await import("../src/limits.ts");
  const many = Array.from(
    { length: MAX_SONGS_PER_REQUEST * 2 + 1 },
    (_, i) => ({
      ...song,
      key: "k" + i,
      title: "Song " + i,
    }),
  );
  const sent = [];
  let failNext = false;
  globalThis.fetch = async (path, options) => {
    const size = JSON.parse(options.body).tracks.length;
    sent.push([
      size,
      new Headers(options.headers).get("X-Pig-Expected-Session"),
    ]);
    // Another account signs in after the first batch: later ones still
    // name the session that asked.
    setSessionId("session-2");
    if (failNext) {
      failNext = false;
      return Response.json({ detail: "Busy", code: "busy" }, { status: 400 });
    }
    failNext = sent.length === 1;
    return Response.json({ created: size, existing: 0, skipped: 0 });
  };
  let shown = [];
  const stop = subscribe((all) => (shown = all));
  await addSongsToLibrary(many);
  const max = MAX_SONGS_PER_REQUEST;
  assert.deepEqual(sent, [
    [max, "session-1"],
    [max, "session-1"],
  ]);
  const error = shown.find((n) => n.kind === "error");
  assert.equal(
    error.text,
    `Adding ${max} songs to your library. Couldn't add the other ${max + 1} songs. Try again.`,
  );
  assert.equal(error.action.label, "Try again");
  await runAction(error.id);
  stop();
  assert.deepEqual(sent.slice(2), [
    [max, "session-1"],
    [1, "session-1"],
  ]);
  assert.match(shown.at(-1).text, new RegExp(`Adding ${max * 2 + 1} songs`));
  clearNotices();
});

test("a collection action keeps the session it was chosen under", async () => {
  // Codex: the songs are read first; a sign-in while they load must not
  // send the action as the new account.
  setSessionId("session-1");
  const sent = serve({
    "/api/v1/requests": { created: 1, existing: 0, skipped: 0 },
  });
  let release;
  const loading = new Promise((r) => (release = r));
  const run = withCollection(
    () => loading.then(() => [song]),
    (list, session) => addSongsToLibrary(list, session),
  );
  setSessionId("session-2");
  release();
  await run;
  assert.deepEqual(sent, []);

  // Unchanged session: the action runs, sent under the session it began in.
  setSessionId("session-1");
  await withCollection(
    async () => [song],
    (list, session) => {
      setSessionId("session-3");
      return addSongsToLibrary(list, session);
    },
  );
  assert.deepEqual(sent, [["POST", "/api/v1/requests", "session-1"]]);
  clearNotices();
});
