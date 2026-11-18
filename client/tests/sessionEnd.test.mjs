// Codex confirmation round (#250, #183): nothing deferred or asked may run
// after the session it was made in has ended. Explicit sign-out sends what
// is pending first; any other session loss cancels it.
import assert from "node:assert/strict";
import test from "node:test";

// The browser's window and document, as the modules listen on them.
globalThis.window = new EventTarget();
globalThis.document = Object.assign(new EventTarget(), {
  visibilityState: "visible",
});

// A signed-in tab: every write names its session (#250).
const { setSessionId } = await import("../src/sessionId.ts");
setSessionId("session-1");
const { defer, drainPending, isPending } = await import("../src/deferred.ts");
const { expectedSession } = await import("../src/sessionId.ts");
const { signOutSession } = await import("../src/signOut.ts");
const { answerConfirm, askToConfirm, subscribeConfirm } = await import(
  "../src/confirmDialog.ts"
);

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const storage = { setItem() {} };
/** What the app does when the session ends (clearSessionReads). */
const sessionCleared = () =>
  window.dispatchEvent(new Event("music-session-cleared"));

test("signing out sends pending deferred writes first, then logs out", async () => {
  const order = [];
  defer("sign-out-a", async (keepalive) => {
    await wait(5);
    order.push(`DELETE playlist keepalive=${keepalive}`);
  });
  await signOutSession(
    async (path, options) =>
      order.push(
        `POST ${path} as ${options.headers["X-Pig-Expected-Session"]}`,
      ),
    "session-1",
    storage,
    drainPending,
  );
  // The logout names the session the click was made under (Codex round 2).
  assert.deepEqual(order, [
    "DELETE playlist keepalive=true",
    "POST /v1/auth/logout as session-1",
  ]);
  assert.equal(isPending("sign-out-a"), false);
});

test("a session lost any other way cancels pending writes: never sent", async () => {
  const sent = [];
  const pending = defer("revoked-a", async () => sent.push("DELETE"), 20);
  sessionCleared();
  assert.equal(await pending.done, false);
  assert.equal(isPending("revoked-a"), false);
  await wait(40);
  assert.deepEqual(sent, []);
});

test("another tab signing in cancels this tab's question and pending write", async (t) => {
  const { openSessionChannel } = await import("../src/sessionChannel.ts");
  const here = openSessionChannel();
  const there = openSessionChannel();
  // An open channel keeps Node running, even after a failed check.
  t.after(() => {
    here.close();
    there.close();
  });
  const stopListening = here.listen(sessionCleared);
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  const sent = [];
  const pending = defer("other-tab-a", async () => sent.push("DELETE"), 50);
  const asked = askToConfirm({
    title: "Sign out everywhere else?",
    body: "Every other browser is signed out.",
    action: "Sign out everywhere else",
    destructive: true,
  });
  const id = open.id;
  there.announce("signed-in");
  assert.equal(await asked, null);
  assert.equal(open, null);
  answerConfirm(id, true);
  assert.equal(await pending.done, false);
  await wait(80);
  assert.deepEqual(sent, []);
  stop();
  stopListening();
});

test("a tab's own announcement doesn't end its own session", async (t) => {
  const { openSessionChannel } = await import("../src/sessionChannel.ts");
  const here = openSessionChannel();
  t.after(() => here.close());
  let heard = 0;
  const stopListening = here.listen(() => heard++);
  here.announce("signed-in");
  await wait(20);
  assert.equal(heard, 0);
  stopListening();
});

test("without BroadcastChannel, other tabs hear it through storage", async () => {
  const { openSessionChannel } = await import("../src/sessionChannel.ts");
  const items = new Map();
  const tabs = [new EventTarget(), new EventTarget()];
  // The browser fires `storage` in every other tab that shares the origin.
  const shared = {
    setItem(key, value) {
      items.set(key, value);
      tabs[0].dispatchEvent(
        Object.assign(new Event("storage"), { key, newValue: value }),
      );
    },
  };
  const here = openSessionChannel({
    BroadcastChannel: null,
    target: tabs[0],
    storage: shared,
  });
  const there = openSessionChannel({
    BroadcastChannel: null,
    target: tabs[1],
    storage: shared,
  });
  const heard = [];
  const stopListening = here.listen((change) => heard.push(change));
  there.announce("signed-out");
  assert.deepEqual(heard, ["signed-out"]);
  stopListening();
});

test("sign-out waits for a send the timer already started (Codex round 2)", async () => {
  const order = [];
  defer(
    "in-flight-a",
    async () => {
      await wait(30);
      order.push("DELETE playlist");
    },
    1,
  );
  await wait(5);
  // Sending already: it has left the pending list, but not the network.
  assert.equal(isPending("in-flight-a"), false);
  await signOutSession(
    async (path) => order.push(`POST ${path}`),
    "session-1",
    storage,
    drainPending,
  );
  assert.deepEqual(order, ["DELETE playlist", "POST /v1/auth/logout"]);
});

test("a stalled send holds sign-out up only for the bound (Codex round 2)", async () => {
  const order = [];
  defer("stalled-a", () => new Promise(() => {}));
  const started = Date.now();
  const done = await signOutSession(
    async (path) => order.push(`POST ${path}`),
    "session-1",
    storage,
    drainPending,
    50,
  );
  assert.equal(done, true);
  assert.deepEqual(order, ["POST /v1/auth/logout"]);
  assert.ok(Date.now() - started < 1000);
});

test("a stalled logout still lets this device sign out (Codex round 2)", async () => {
  const kept = new Map();
  const done = await signOutSession(
    () => new Promise(() => {}),
    "session-1",
    { setItem: (key, value) => kept.set(key, value) },
    async () => {},
    50,
  );
  // Treated as unreachable: ended by its id on the next start (#80).
  assert.equal(done, false);
  assert.deepEqual([...kept.values()], ["session-1"]);
});

test("a deferred write names the session it was scheduled under (Codex round 2)", async () => {
  setSessionId("session-1");
  let named = null;
  const pending = defer("named-a", async () => (named = expectedSession()), 5);
  // Another sign-in arrives before it is sent.
  setSessionId("session-2");
  assert.equal(await pending.done, true);
  assert.equal(named, "session-1");
  setSessionId("session-1");
});

test("with no session known, a deferred write isn't started at all", async () => {
  setSessionId(null);
  const sent = [];
  const pending = defer("unknown-a", async () => sent.push("DELETE"), 5);
  await assert.rejects(pending.done, /Couldn't tell who is signed in/);
  assert.equal(isPending("unknown-a"), false);
  await wait(20);
  assert.deepEqual(sent, []);
  setSessionId("session-1");
});
