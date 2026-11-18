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
