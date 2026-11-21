// Codex round 1 on #267: one newest playing choice for the whole app.
import assert from "node:assert/strict";
import test from "node:test";
import { chooseReplace, replaced } from "../src/playChoice.ts";

test("a replacing choice gives way to a newer one or a queue replaced at once", () => {
  const first = chooseReplace();
  const second = chooseReplace();
  assert.equal(first(), false);
  assert.equal(second(), true);
  replaced();
  assert.equal(second(), false);
  const third = chooseReplace();
  assert.equal(third(), true);
});
