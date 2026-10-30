import assert from "node:assert/strict";
import test from "node:test";

const { signOutSession, finishSignOut, signedInAgain } = await import(
  "../src/signOut.ts"
);

function memory() {
  const items = new Map();
  return {
    items,
    getItem: (k) => items.get(k) ?? null,
    setItem: (k, v) => items.set(k, v),
    removeItem: (k) => items.delete(k),
  };
}

const unreachable = Object.assign(new TypeError("Failed to fetch"), {});
const signedOut = Object.assign(new Error("Please sign in"), {
  code: "authentication_required",
});

test("signing out while the server is unreachable clears this device and remembers the session (#80)", async () => {
  const storage = memory();
  const done = await signOutSession(
    async () => {
      throw unreachable;
    },
    "session-1",
    storage,
  );
  assert.equal(done, false);
  assert.deepEqual([...storage.items.values()], ["session-1"]);
});

test("a later start ends only that session by its id, never through logout (#80)", async () => {
  const storage = memory();
  storage.setItem("pig-music-pending-sign-out", "session-1");
  const calls = [];
  const call = async (path, options) => {
    calls.push(`${options?.method ?? "GET"} ${path}`);
  };
  assert.equal(await finishSignOut(call, storage), true);
  // A logout request would clear the cookie of a newer sign-in in another tab.
  assert.deepEqual(calls, ["DELETE /v1/me/sessions/session-1"]);
  assert.equal(storage.items.size, 0);
});

test("the pending sign-out waits while the server is still unreachable", async () => {
  const storage = memory();
  storage.setItem("pig-music-pending-sign-out", "session-1");
  const offline = async () => {
    throw unreachable;
  };
  assert.equal(await finishSignOut(offline, storage), false);
  assert.equal(storage.items.size, 1);
  const expired = async () => {
    throw signedOut;
  };
  assert.equal(await finishSignOut(expired, storage), true);
  assert.equal(storage.items.size, 0);
});

test("a sign-out whose session was never known stays signed out until a fresh sign-in (Codex round 1)", async () => {
  const storage = memory();
  const calls = [];
  const call = async (path) => {
    calls.push(path);
    throw unreachable;
  };
  assert.equal(await signOutSession(call, null, storage), false);
  // Reopened with the server back: the old cookie must not sign back in.
  assert.equal(await finishSignOut(async () => {}, storage), false);
  signedInAgain(storage);
  assert.equal(await finishSignOut(async () => {}, storage), true);
  assert.deepEqual(calls, ["/v1/auth/logout"]);
});

test("a sign-in as someone else ends the pending sign-out (Codex round 1)", async () => {
  const storage = memory();
  storage.setItem("pig-music-pending-sign-out", "session-1");
  const notTheirs = Object.assign(new Error("Not found"), {
    code: "session_not_found",
  });
  assert.equal(
    await finishSignOut(async () => {
      throw notTheirs;
    }, storage),
    true,
  );
  assert.equal(storage.items.size, 0);
});
