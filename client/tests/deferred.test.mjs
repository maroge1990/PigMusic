import assert from "node:assert/strict";
import test from "node:test";
import { registerHooks } from "node:module";
registerHooks({
  resolve(specifier, context, next) {
    try {
      return next(specifier, context);
    } catch (error) {
      if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier))
        return next(specifier + ".ts", context);
      throw error;
    }
  },
});

const { defer, flushPending, isPending } = await import("../src/deferred.ts");

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

test("an action is sent once its Undo time has passed", async () => {
  const sent = [];
  const action = defer("a", async (keepalive) => sent.push(keepalive), 20);
  assert.ok(isPending("a"));
  await wait(5);
  assert.deepEqual(sent, []);
  assert.equal(await action.done, true);
  assert.deepEqual(sent, [false]);
  assert.ok(!isPending("a"));
  assert.equal(action.undo(), false); // Too late: already sent.
});

test("Undo cancels it and nothing is sent", async () => {
  const sent = [];
  const action = defer("b", async () => sent.push("b"), 20);
  assert.equal(action.undo(), true);
  assert.equal(await action.done, false);
  await wait(40);
  assert.deepEqual(sent, []);
});

test("a page going away sends what is pending at once, with keepalive", async () => {
  const sent = [];
  const first = defer("c", async (keepalive) => sent.push(["c", keepalive]));
  const second = defer("d", async (keepalive) => sent.push(["d", keepalive]));
  flushPending();
  assert.equal(await first.done, true);
  assert.equal(await second.done, true);
  assert.deepEqual(sent, [
    ["c", true],
    ["d", true],
  ]);
  assert.equal(first.undo(), false);
});

test("a failed send is reported, and a repeated key sends the earlier first", async () => {
  const failing = defer(
    "e",
    async () => {
      throw new Error("conflict");
    },
    10,
  );
  await assert.rejects(failing.done, /conflict/);
  const sent = [];
  const earlier = defer("f", async () => sent.push("earlier"), 1000);
  const later = defer("f", async () => sent.push("later"), 10);
  assert.equal(await earlier.done, true);
  assert.equal(await later.done, true);
  assert.deepEqual(sent, ["earlier", "later"]);
});
