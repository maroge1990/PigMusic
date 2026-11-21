// Codex round 1 on #267: one newest playing choice for the whole app.
import assert from "node:assert/strict";
import test from "node:test";
import { choosePlay, played } from "../src/playChoice.ts";

test("a playing choice gives way to a newer choice or anything played", () => {
  const first = choosePlay();
  const second = choosePlay();
  assert.equal(first(), false);
  assert.equal(second(), true);
  played();
  assert.equal(second(), false);
  const third = choosePlay();
  assert.equal(third(), true);
});
