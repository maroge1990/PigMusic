import assert from "node:assert/strict";
import test from "node:test";

const { startFailure } = await import("../src/playStart.ts");

test("a skipped song's late play() rejection shows nothing (#80)", () => {
  const blocked = new DOMException("needs a gesture", "NotAllowedError");
  const aborted = new DOMException("interrupted by a new load", "AbortError");
  assert.equal(startFailure(blocked, true), "");
  assert.equal(startFailure(aborted, false), "");
  assert.equal(
    startFailure(blocked, false),
    "Tap Play to start audio on this device.",
  );
});
