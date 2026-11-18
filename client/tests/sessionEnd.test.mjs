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

const { defer, flushPending, isPending } = await import("../src/deferred.ts");
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
    async (path) => order.push(`POST ${path}`),
    "s1",
    storage,
    flushPending,
  );
  assert.deepEqual(order, [
    "DELETE playlist keepalive=true",
    "POST /v1/auth/logout",
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

test("another tab signing in cancels this tab's question and pending write", async () => {
  const { openSessionChannel } = await import("../src/sessionChannel.ts");
  const here = openSessionChannel();
  const there = openSessionChannel();
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
  assert.equal(await asked, false);
  assert.equal(open, null);
  answerConfirm(id, true);
  assert.equal(await pending.done, false);
  await wait(80);
  assert.deepEqual(sent, []);
  stop();
  stopListening();
  here.close();
  there.close();
});

test("a tab's own announcement doesn't end its own session", async () => {
  const { openSessionChannel } = await import("../src/sessionChannel.ts");
  const here = openSessionChannel();
  let heard = 0;
  const stopListening = here.listen(() => heard++);
  here.announce("signed-in");
  await wait(20);
  assert.equal(heard, 0);
  stopListening();
  here.close();
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
