// #267 (D-129): a catalogue album's card acts on the edition its page opens
// on, adds what the page's Add would add, and acts once per choice.
import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = { addEventListener() {}, dispatchEvent() {} };
globalThis.Event = class {
  constructor(type) {
    this.type = type;
  }
};
const {
  addable,
  afterAdd,
  cardMark,
  checking,
  lookedUp,
  markYours,
  nothingLeftToAdd,
  nothingToAdd,
  openingEdition,
} = await import("../src/catalogueAlbum.ts");

test("after an Add each song is as the server took it (Codex round 1)", () => {
  const r = (key, state) => ({ key, title: key, artist: "A", state });
  const rows = [
    r("own", "saved"),
    r("a", "missing"),
    r("b", "server"),
    r("c", "missing"),
    r("d", "missing"),
    r("e", "missing"),
    r("f", "pending"),
  ];
  // Sent: a, b, c, d, e (the addable ones, in order).
  const now = afterAdd(rows, [
    "created",
    "added",
    "excluded",
    "paused",
    "in_library",
  ]);
  assert.deepEqual(
    now.map((r) => r.state),
    ["saved", "pending", "saved", "missing", "paused", "saved", "pending"],
  );
  // The excluded song can be added again; the mark waits for it.
  assert.equal(nothingLeftToAdd(now), false);
  assert.equal(cardMark(now), null);
  // An answer that doesn't say song by song tells nothing.
  assert.equal(afterAdd(rows, undefined), null);
  assert.equal(afterAdd(rows, ["created"]), null);
  // A paused song is not shown as Adding, but leaves nothing to add.
  const paused = [r("a", "paused"), r("b", "pending")];
  assert.equal(cardMark(paused), null);
  assert.equal(nothingLeftToAdd(paused), true);
});
const { collectionMenu, isLatest, latestChoice } = await import(
  "../src/menus.ts"
);
const { setSessionId } = await import("../src/sessionId.ts");
const {
  CatalogueRefusal,
  NO_EDITIONS,
  addCatalogueAlbum,
  cardFailure,
  catalogueAlbumSongs,
  catalogueArtistSongs,
} = await import("../src/catalogueActions.ts");
const { downloadSongs, withCollection } = await import("../src/songActions.ts");
const { clearSessionReads } = await import("../src/api.ts");
const { clearNotices, subscribe } = await import("../src/notices.ts");

const row = (key, state, extra = {}) => ({
  key,
  title: key,
  artist: "A",
  state,
  ...extra,
});

test("a release group opens on its recommended edition, else its first", () => {
  const editions = [{ id: "live" }, { id: "studio" }];
  assert.equal(
    openingEdition({ editions, total: 2, recommended_id: "studio" }),
    "studio",
  );
  assert.equal(openingEdition({ editions, total: 2 }), "live");
  assert.equal(
    openingEdition({ editions: [], total: 0, recommended_id: "x" }),
    undefined,
  );
});

test("an explicit edition keeps its own state; the rest wait for the lookup", () => {
  const plain = [
    row("a", "missing"),
    row("b", "missing", { explicit_edition: true }),
    row("c", "saved"),
  ];
  assert.deepEqual(
    lookedUp(plain).map((r) => r.key),
    ["a", "c"],
  );
  const waiting = checking(plain);
  assert.deepEqual(
    waiting.map((r) => !!r.checking),
    [true, false, false],
  );
  assert.deepEqual(
    waiting.filter(addable).map((r) => r.key),
    ["b"],
  );
  const found = markYours(plain, [row("a", "saved"), row("c", "saved")]);
  assert.deepEqual(
    found.map((r) => r.state),
    ["saved", "missing", "saved"],
  );
});

test("the card's mark: none while songs are to add, else yours or adding", () => {
  assert.equal(cardMark(null), null);
  assert.equal(cardMark([row("a", "missing"), row("b", "saved")]), null);
  assert.equal(
    cardMark([row("a", "missing", { checking: true }), row("b", "saved")]),
    null,
  );
  assert.equal(cardMark([row("a", "saved"), row("b", "saved")]), "library");
  assert.equal(cardMark([row("a", "pending"), row("b", "saved")]), "requested");
  assert.equal(
    nothingToAdd([row("a", "saved")]),
    "Already in your library: nothing to add.",
  );
  assert.equal(
    nothingToAdd([row("a", "pending"), row("b", "saved")]),
    "Nothing to add: every song is in your library or being added.",
  );
});

test("Play chosen again replaces the first; Add chosen again while on its way does nothing", () => {
  const latest = new Map();
  const first = latestChoice(latest, "play");
  const second = latestChoice(latest, "play");
  assert.equal(isLatest(latest, first), false);
  assert.equal(isLatest(latest, second), true);
  const add = latestChoice(latest, "add");
  assert.ok(add);
  assert.equal(latestChoice(latest, "add"), null);
  add.done = true;
  assert.ok(latestChoice(latest, "add"));
  // Kinds are separate: an Add on its way leaves Download free.
  assert.ok(latestChoice(latest, "download"));
});

function serve(answers) {
  const sent = [];
  globalThis.fetch = async (path, options) => {
    sent.push([
      options?.method || "GET",
      path,
      options?.body ? JSON.parse(options.body) : undefined,
      new Headers(options?.headers).get("X-Pig-Expected-Session"),
    ]);
    const answer = answers[path];
    if (typeof answer === "function") return answer();
    return Response.json(answer ?? {});
  };
  return sent;
}

const release = (id, titles) => ({
  id,
  title: "Album",
  artist: "A",
  date: "2025",
  description: "",
  tracks: titles.map((title, i) => ({
    id: `${id}-${i}`,
    title,
    artist: "A",
    duration: 100,
    disc_number: 1,
    track_number: i + 1,
    status: "",
    presence_note: "",
    server_state: "not_in_library",
    musicbrainz_recording_id: `${id}-rec-${i}`,
  })),
});

test("a card's songs are its page's: the recommended edition, looked up", async () => {
  clearSessionReads();
  setSessionId("s1");
  const sent = serve({
    "/api/v1/catalogue/groups/g1/editions": {
      editions: [{ id: "live" }, { id: "studio" }],
      total: 2,
      recommended_id: "studio",
    },
    "/api/v1/catalogue/releases/studio": release("studio", ["One", "Two"]),
    "/api/v1/library/lookup": {
      tracks: [{ id: "mine", title: "One", artist: "A" }, null],
    },
  });
  const songs = await catalogueAlbumSongs("g1", "mb-a");
  assert.deepEqual(
    songs.map((s) => [s.title, s.state, s.release_id, s.artist_id]),
    [
      ["One", "saved", "studio", "mb-a"],
      ["Two", "missing", "studio", "mb-a"],
    ],
  );
  assert.ok(!sent.some(([, path]) => path.includes("releases/live")));
});

test("a failed library lookup leaves the songs playable but not addable", async () => {
  clearSessionReads();
  setSessionId("s1");
  serve({
    "/api/v1/catalogue/groups/g2/editions": {
      editions: [{ id: "r2" }],
      total: 1,
    },
    "/api/v1/catalogue/releases/r2": release("r2", ["One"]),
    "/api/v1/library/lookup": () =>
      Response.json({ detail: "nope" }, { status: 400 }),
  });
  const songs = await catalogueAlbumSongs("g2", "mb-a");
  assert.equal(songs.length, 1);
  assert.equal(songs[0].checking, true);
  const notices = [];
  clearNotices();
  const stop = subscribe((list) => notices.splice(0, notices.length, ...list));
  const sent = serve({});
  assert.equal(await addCatalogueAlbum(songs, "s1"), null);
  stop();
  assert.deepEqual(sent, []);
  assert.ok(
    notices.some((n) => n.text === "Couldn't check your library. Try again."),
  );
});

test("Add from a card sends only the songs to add, under the session that chose it", async () => {
  clearSessionReads();
  setSessionId("s2");
  const sent = serve({
    "/api/v1/requests": {
      created: 1,
      existing: 0,
      skipped: 0,
      outcomes: ["created"],
    },
  });
  const rows = [
    row("a", "saved", { saved: { id: "x" } }),
    row("b", "missing"),
    row("c", "pending"),
  ];
  const now = await addCatalogueAlbum(rows, "s1");
  assert.equal(sent.length, 1);
  const [method, path, body, session] = sent[0];
  assert.equal(method, "POST");
  assert.equal(path, "/api/v1/requests");
  assert.deepEqual(
    body.tracks.map((t) => t.title),
    ["b"],
  );
  assert.equal(session, "s1");
  assert.deepEqual(
    now.map((r) => r.state),
    ["saved", "pending", "pending"],
  );
  assert.equal(cardMark(now), "requested");
});

test("Add from a card past a queue limit says so and leaves those songs to add again", async () => {
  // D-128 (#242): a song a limit left out is `excluded`, its position in
  // over_limit; the card offers it again and says why it wasn't added.
  clearSessionReads();
  setSessionId("s1");
  serve({
    "/api/v1/requests": {
      created: 1,
      existing: 0,
      skipped: 0,
      left_out: 1,
      over_limit: [1],
      limit: "rate",
      retry_after: 90,
      outcomes: ["created", "excluded"],
    },
  });
  let shown = [];
  const stop = subscribe((all) => (shown = all));
  const now = await addCatalogueAlbum(
    [row("a", "missing"), row("b", "missing")],
    "s1",
  );
  stop();
  assert.deepEqual(
    now.map((r) => r.state),
    ["pending", "missing"],
  );
  assert.equal(nothingLeftToAdd(now), false);
  assert.equal(cardMark(now), null);
  assert.equal(
    shown.at(-1).text,
    "Adding 1 song to your library. Couldn't add 1 song: too many are waiting. Try again later.",
  );
  clearNotices();
});

test("a collection's songs read after another sign-in act for nobody, and fail silently", async () => {
  setSessionId("s1");
  let acted = false;
  await withCollection(
    async () => {
      setSessionId("s2");
      return [row("a", "missing")];
    },
    () => (acted = true),
  );
  assert.equal(acted, false);
  setSessionId("s1");
  await withCollection(
    async () => {
      clearSessionReads();
      setSessionId("s1");
      throw new Error("late failure");
    },
    () => (acted = true),
  );
  assert.equal(acted, false);
});

test("a catalogue artist's menu has no Add to library; other menus keep it", () => {
  const words = (groups) => groups.flat();
  assert.deepEqual(words(collectionMenu("artist", false)), [
    "play",
    "shuffle",
    "play-next",
    "add-to-playlist",
    "download",
  ]);
  assert.ok(words(collectionMenu("artist")).includes("add-to-library"));
  assert.ok(words(collectionMenu("album")).includes("add-to-library"));
});

test("a card's failure keeps the server's sentence, else says Couldn't load", () => {
  assert.equal(
    cardFailure(new CatalogueRefusal("Release not found"), "X").message,
    "Release not found",
  );
  assert.equal(cardFailure(new Error(NO_EDITIONS), "X").message, NO_EDITIONS);
  assert.equal(
    cardFailure(new Error("Catalogue unavailable"), "Album one").message,
    "Couldn't load Album one. Try again.",
  );
  assert.equal(
    cardFailure(new TypeError("Failed to fetch"), "Pop star").message,
    "Couldn't load Pop star. Try again.",
  );
});

test("an artist card's songs are the page's popular songs, in order, yours marked", async () => {
  clearSessionReads();
  setSessionId("s1");
  serve({
    "/api/v1/catalogue/artists/mb-p/popularity": {
      songs: [
        { id: "r1", title: "One", artist: "P", album: "H", release_id: "h" },
        { id: "r2", title: "Two", artist: "P", album: "H", release_id: "h" },
      ],
      albums: [],
    },
    "/api/v1/library/lookup": {
      tracks: [null, { id: "mine", title: "Two", artist: "P" }],
    },
  });
  const songs = await catalogueArtistSongs("mb-p");
  assert.deepEqual(
    songs.map((s) => [s.title, s.state, s.recording_id, s.artist_id]),
    [
      ["One", "missing", "r1", "mb-p"],
      ["Two", "saved", "r2", "mb-p"],
    ],
  );
  clearSessionReads();
  setSessionId("s1");
  serve({
    "/api/v1/catalogue/artists/mb-e/popularity": { songs: [], albums: [] },
  });
  await assert.rejects(catalogueArtistSongs("mb-e"));
});

test("downloads still waiting when the session ends are not made", async () => {
  clearSessionReads();
  setSessionId("s1");
  const clicked = [];
  globalThis.document = {
    createElement: () => ({
      click() {
        clicked.push(this.href);
      },
      remove() {},
    }),
    body: { append() {} },
  };
  const file = (n) => row("s" + n, "saved", { saved: { id: "f" + n } });
  downloadSongs([file(1), file(2), file(3)]);
  // The first goes at once; the session ends before the others.
  await new Promise((r) => setTimeout(r, 50));
  clearSessionReads();
  await new Promise((r) => setTimeout(r, 800));
  assert.deepEqual(clicked, ["/api/v1/tracks/f1/file"]);
  delete globalThis.document;
});
