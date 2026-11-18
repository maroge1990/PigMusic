// #250: one notice component for the whole web client.
import assert from "node:assert/strict";
import { mock, test } from "node:test";

const {
  MAX_SHOWN,
  NOTICE_SHOWN_MS,
  UNDO_SHOWN_MS,
  clearNotices,
  dismiss,
  notify,
  notifyError,
  offerUndo,
  roleOf,
  runAction,
  shown,
  subscribe,
} = await import("../src/notices.ts");

/** The notices as the area would show them, after each change. */
function watch() {
  let all = [];
  const stop = subscribe((now) => (all = now));
  return { now: () => all, shown: () => shown(all), stop };
}
const flush = () => new Promise((r) => setImmediate(r));

test.beforeEach(() => {
  mock.timers.enable({ apis: ["setTimeout"] });
  clearNotices();
});
test.afterEach(() => mock.timers.reset());

test("success is polite and errors interrupt", () => {
  assert.equal(roleOf("info"), "status");
  assert.equal(roleOf("action"), "status");
  assert.equal(roleOf("error"), "alert");
});

test("a success clears itself after about 5 seconds", () => {
  const area = watch();
  notify("Added to playlist");
  assert.deepEqual(
    area.now().map((n) => [n.kind, n.text]),
    [["info", "Added to playlist"]],
  );
  mock.timers.tick(NOTICE_SHOWN_MS - 1);
  assert.equal(area.now().length, 1);
  mock.timers.tick(1);
  assert.equal(area.now().length, 0);
  area.stop();
});

test("an Undo stays for its 15 seconds", () => {
  const area = watch();
  offerUndo("Playlist deleted.", () => {});
  mock.timers.tick(NOTICE_SHOWN_MS);
  assert.equal(area.now()[0].action.label, "Undo");
  mock.timers.tick(UNDO_SHOWN_MS - NOTICE_SHOWN_MS - 1);
  assert.equal(area.now().length, 1);
  mock.timers.tick(1);
  assert.equal(area.now().length, 0);
  area.stop();
});

test("an Undo leaves when its deferred action starts sending", async () => {
  const area = watch();
  let start;
  offerUndo(
    "Queue cleared.",
    () => {},
    "Back",
    new Promise((r) => (start = r)),
  );
  start();
  await flush();
  assert.equal(area.now().length, 0);
  area.stop();
});

test("an error stays until closed", () => {
  const area = watch();
  const id = notifyError("Couldn't save. Try again.");
  mock.timers.tick(60_000);
  assert.equal(area.now()[0].kind, "error");
  dismiss(id);
  assert.equal(area.now().length, 0);
  area.stop();
});

test("a new error replaces the last one", () => {
  const area = watch();
  notifyError("Couldn't save. Try again.");
  notify("Saved");
  notifyError("Couldn't add. Try again.");
  assert.deepEqual(
    area.now().map((n) => n.text),
    ["Saved", "Couldn't add. Try again."],
  );
  area.stop();
});

test("every kind can be closed", () => {
  const area = watch();
  for (const id of [
    notify("Saved"),
    offerUndo("Removed.", () => {}),
    notifyError("Couldn't. Try again."),
  ])
    dismiss(id);
  assert.equal(area.now().length, 0);
  // Closing early leaves no timer behind to fire later.
  mock.timers.tick(UNDO_SHOWN_MS);
  assert.equal(area.now().length, 0);
  area.stop();
});

test("notices stack newest last, at most three shown", () => {
  const area = watch();
  for (const text of ["one", "two", "three", "four"]) notify(text);
  assert.equal(MAX_SHOWN, 3);
  assert.deepEqual(
    area.shown().map((n) => n.text),
    ["two", "three", "four"],
  );
  area.stop();
});

test("later notices never hide a live Undo before it is sent (Codex round 1)", () => {
  const area = watch();
  // A deferred delete: sent after 10 s unless Undo is pressed.
  offerUndo("Road trip deleted.", () => {}, "Back", new Promise(() => {}));
  for (let second = 1; second <= 10; second++) {
    notify(`Saved ${second}`);
    notifyError(`Couldn't ${second}. Try again.`);
    mock.timers.tick(1_000);
    assert.equal(area.shown().length, MAX_SHOWN);
    assert.equal(area.shown()[0].text, "Road trip deleted.", `at ${second} s`);
  }
  // Routine notices keep their order after the Undo, newest last.
  assert.deepEqual(
    area.shown().map((n) => n.text),
    ["Road trip deleted.", "Saved 10", "Couldn't 10. Try again."],
  );
  area.stop();
});

test("more live Undos than fit are all shown, routine notices dropped", () => {
  const area = watch();
  notify("Saved");
  for (const name of ["A", "B", "C", "D"])
    offerUndo(`${name} deleted.`, () => {});
  notify("Moved up");
  assert.deepEqual(
    area.shown().map((n) => n.text),
    ["A deleted.", "B deleted.", "C deleted.", "D deleted."],
  );
  area.stop();
});

test("page-hide sends each pending action once with keepalive; an undone one never", async () => {
  const { defer, flushPending } = await import("../src/deferred.ts");
  const { setSessionId } = await import("../src/sessionId.ts");
  setSessionId("session-1");
  const area = watch();
  const sent = [];
  const offer = (name) => {
    const pending = defer(`page-hide-${name}`, async (keepalive) =>
      sent.push([name, keepalive]),
    );
    return offerUndo(
      `${name} deleted.`,
      () => {
        if (!pending.undo()) throw new Error("It was deleted already.");
      },
      `${name} is back.`,
      pending.started,
    );
  };
  offer("A");
  offer("B");
  await runAction(offer("C"));
  flushPending();
  flushPending();
  await flush();
  assert.deepEqual(sent.sort(), [
    ["A", true],
    ["B", true],
  ]);
  // Once sent, their Undo is gone; the undone one says it is back.
  assert.deepEqual(
    area.now().map((n) => n.text),
    ["C is back."],
  );
  area.stop();
});

test("a notice with the same key replaces the earlier one", () => {
  const area = watch();
  notify("Added to favourites", "favourite");
  notify("Removed from favourites", "favourite");
  assert.deepEqual(
    area.now().map((n) => n.text),
    ["Removed from favourites"],
  );
  area.stop();
});

test("Undo runs, then says what came back", async () => {
  const area = watch();
  let undone = 0;
  const id = offerUndo(
    "Playlist deleted.",
    () => undone++,
    "Playlist is back.",
  );
  await runAction(id);
  assert.equal(undone, 1);
  assert.deepEqual(
    area.now().map((n) => [n.kind, n.text]),
    [["info", "Playlist is back."]],
  );
  area.stop();
});

test("an Undo that fails says it couldn't", async () => {
  const area = watch();
  const id = offerUndo("Playlist deleted.", () => {
    throw new Error("It was deleted already.");
  });
  await runAction(id);
  assert.deepEqual(
    area.now().map((n) => [n.kind, n.text]),
    [["error", "Couldn't undo. It was deleted already."]],
  );
  area.stop();
});

test("Try again closes the error and runs the action again", async () => {
  const area = watch();
  let tries = 0;
  const id = notifyError("Couldn't add. Try again.", () => tries++);
  assert.equal(area.now()[0].action.label, "Try again");
  await runAction(id);
  assert.equal(tries, 1);
  assert.equal(area.now().length, 0);
  area.stop();
});
