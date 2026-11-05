import assert from "node:assert/strict";
import test from "node:test";

const q = await import("../src/queue.ts");

const song = (key, extra = {}) => ({
  key,
  title: key,
  artist: "A",
  state: "saved",
  ...extra,
});
const keys = (songs) => songs.map((s) => s.key);

test("skipping forward stops at the end unless repeat all wraps", () => {
  assert.equal(q.stepIndex(3, 1, 1, "off"), 2);
  assert.equal(q.stepIndex(3, 2, 1, "off"), 3); // past the end: nothing there
  assert.equal(q.stepIndex(3, 2, 1, "one"), 3);
  assert.equal(q.stepIndex(3, 2, 1, "all"), 0);
  // Up next can jump several songs ahead.
  assert.equal(q.stepIndex(5, 0, 3, "off"), 3);
});

test("skipping back wraps to the last song only with repeat all", () => {
  assert.equal(q.stepIndex(3, 0, -1, "off"), -1);
  assert.equal(q.stepIndex(3, 0, -1, "all"), 2);
  assert.equal(q.stepIndex(0, 0, -1, "all"), -1); // an empty queue never wraps
});

test("a finished song plays the next one, or itself again with repeat one", () => {
  assert.equal(q.afterEnd("off"), "next");
  assert.equal(q.afterEnd("all"), "next");
  assert.equal(q.afterEnd("one"), "again");
});

test("Next is offered while something follows, on radio, or with repeat all", () => {
  const songs = [song("a"), song("b")];
  assert.equal(q.hasNext(songs, 0, "off"), true);
  assert.equal(q.hasNext(songs, 1, "off"), false);
  assert.equal(q.hasNext(songs, 1, "one"), false);
  assert.equal(q.hasNext(songs, 1, "all"), true);
  assert.equal(q.hasNext(songs, -1, "all"), false); // nothing loaded
  assert.equal(q.hasNext([song("r", { radio: true })], 0, "off"), true);
});

test("radio waits for more songs instead of stopping at its end", () => {
  const radio = [song("r", { radio: true })];
  assert.equal(q.waitsForRadio(radio, 0, 1), true);
  assert.equal(q.waitsForRadio(radio, 0, -1), false);
  assert.equal(q.waitsForRadio([song("a")], 0, 1), false);
});

test("Previous restarts a song heard past three seconds, or the first song", () => {
  assert.equal(q.previousRestarts(3.5, 2), true);
  assert.equal(q.previousRestarts(1, 0), true);
  assert.equal(q.previousRestarts(3, 2), false);
  assert.equal(q.canGoPrevious(false, true, 0), true);
  assert.equal(q.canGoPrevious(false, false, 0), false);
  assert.equal(q.canGoPrevious(false, false, 1), true);
  assert.equal(q.canGoPrevious(true, true, 1), false); // still loading
});

test("repeat cycles off, all, one and reads back what was saved", () => {
  assert.equal(q.nextRepeat("off"), "all");
  assert.equal(q.nextRepeat("all"), "one");
  assert.equal(q.nextRepeat("one"), "off");
  assert.equal(q.parseRepeat("one"), "one");
  assert.equal(q.parseRepeat("all"), "all");
  assert.equal(q.parseRepeat("sideways"), "off");
  assert.equal(q.parseRepeat(null), "off");
});

test("a radio skip steers the station only before it counts as a listen", () => {
  const radio = song("r", { radio: true, cache_id: "c1" });
  assert.equal(q.skipSteersRadio(radio, undefined, 30), true);
  assert.equal(q.skipSteersRadio(radio, 12, 30), true);
  assert.equal(q.skipSteersRadio(radio, 30, 30), false);
  assert.equal(q.skipSteersRadio(song("a", { cache_id: "c" }), 0, 30), false);
  assert.equal(q.skipSteersRadio(song("r", { radio: true }), 0, 30), false);
  assert.equal(q.skipSteersRadio(null, 0, 30), false);
});

test("radio refills when five or fewer songs are left, without repeats", () => {
  const radio = Array.from({ length: 8 }, (_, i) =>
    song("r" + i, { radio: true, cache_id: "c" + i }),
  );
  assert.equal(q.radioRunningLow(radio[1], radio, 1), false); // six left
  assert.equal(q.radioRunningLow(radio[2], radio, 2), true); // five left
  assert.equal(q.radioRunningLow(song("a"), [song("a")], 0), false);
  assert.equal(q.lastRadioSong([song("x"), ...radio, song("y")]).key, "r7");
  assert.equal(q.lastRadioSong([song("x")]), undefined);
  const batch = [
    song("dup", { cache_id: "c3" }),
    song("new", { cache_id: "c99" }),
  ];
  assert.deepEqual(keys(q.freshRadioSongs(radio, batch)), ["new"]);
});

test("only the next two streamed songs are prepared ahead", () => {
  const songs = [
    song("now"),
    song("stream1", { state: "missing" }),
    song("saved", { saved: { id: "t" } }),
    song("stream2", { state: "missing" }),
  ];
  // Two ahead are looked at; the saved one needs no preparing.
  assert.deepEqual(keys(q.upcomingToPrepare(songs, 0)), ["stream1"]);
  assert.deepEqual(keys(q.upcomingToPrepare(songs, 1)), ["stream2"]);
  assert.deepEqual(
    keys(
      q.upcomingToPrepare(
        [
          song("now"),
          song("mix", { state: "missing", cache_id: "c" }),
          song("server", { state: "server" }),
        ],
        0,
      ),
    ),
    [],
  );
  assert.deepEqual(q.upcomingToPrepare(songs, 3), []);
});

test("Play next goes straight after the current song", () => {
  const songs = [song("a"), song("b"), song("c")];
  q.insertNext(songs, 0, song("x"));
  assert.deepEqual(keys(songs), ["a", "x", "b", "c"]);
});

test("reordering Up next never moves the current or earlier songs", () => {
  const songs = [song("a"), song("b"), song("c"), song("d")];
  assert.equal(q.moveUpcoming(songs, 1, 3, -1), true);
  assert.deepEqual(keys(songs), ["a", "b", "d", "c"]);
  assert.equal(q.moveUpcoming(songs, 1, 2, -1), false); // onto the current one
  assert.equal(q.moveUpcoming(songs, 1, 3, 1), false); // past the end
  assert.deepEqual(keys(songs), ["a", "b", "d", "c"]);
  assert.equal(q.moveUpcoming(songs, 1, 2, 1), true);
  assert.deepEqual(keys(songs), ["a", "b", "c", "d"]);
});

test("removing from Up next leaves the current and earlier songs", () => {
  const songs = [song("a"), song("b"), song("c")];
  assert.equal(q.removeUpcoming(songs, 1, 1), false);
  assert.equal(q.removeUpcoming(songs, 1, 0), false);
  assert.equal(q.removeUpcoming(songs, 1, 2), true);
  assert.deepEqual(keys(songs), ["a", "b"]);
});

test("a saved queue resumes at its song and position", () => {
  const songs = [song("a"), song("b")];
  const text = q.serializeQueue(songs, 1, 42.5);
  assert.deepEqual(q.parseSavedQueue(text), {
    songs,
    cursor: 1,
    position: 42.5,
  });
  // An empty queue or no song playing clears what was saved.
  assert.equal(q.serializeQueue([], 0, 0), null);
  assert.equal(q.serializeQueue(songs, -1, 0), null);
});

test("a long queue saves its first 500 songs and stays inside them", () => {
  const songs = Array.from({ length: 600 }, (_, i) => song("s" + i));
  const saved = q.parseSavedQueue(q.serializeQueue(songs, 550, 7));
  assert.equal(saved.songs.length, 500);
  assert.equal(saved.cursor, 499);
  assert.equal(saved.songs[saved.cursor].key, "s499");
});

test("a damaged or empty saved queue is ignored", () => {
  assert.equal(q.parseSavedQueue(null), null);
  assert.equal(q.parseSavedQueue("not json"), null);
  assert.equal(q.parseSavedQueue('{"songs":"x","cursor":0}'), null);
  assert.equal(q.parseSavedQueue('{"songs":[],"cursor":0}'), null);
  assert.equal(
    q.parseSavedQueue('{"songs":[{"key":"a"}],"cursor":3}'),
    null, // the cursor points past the songs
  );
  assert.deepEqual(
    q.parseSavedQueue('{"songs":[{"key":"a"}],"cursor":0,"position":"x"}'),
    { songs: [{ key: "a" }], cursor: 0, position: 0 },
  );
});
