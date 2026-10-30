import assert from "node:assert/strict";
import test from "node:test";

const { signOutSession, finishSignOut, currentSession } = await import(
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

test("the current session is the one the server marks current", async () => {
  const call = async () => ({
    sessions: [
      { id: "other", current: false },
      { id: "mine", current: true },
    ],
  });
  assert.equal(await currentSession(call), "mine");
  assert.equal(
    await currentSession(async () => {
      throw unreachable;
    }),
    null,
  );
});
