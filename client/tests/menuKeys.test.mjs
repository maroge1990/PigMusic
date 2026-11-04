import assert from "node:assert/strict";
import test from "node:test";
import { nextMenuIndex } from "../src/menuKeys.ts";

test("arrow keys wrap around a menu; Home and End jump to its ends", () => {
  assert.equal(nextMenuIndex("ArrowDown", 0, 4), 1);
  assert.equal(nextMenuIndex("ArrowDown", 3, 4), 0);
  assert.equal(nextMenuIndex("ArrowUp", 0, 4), 3);
  assert.equal(nextMenuIndex("ArrowUp", 2, 4), 1);
  assert.equal(nextMenuIndex("Home", 2, 4), 0);
  assert.equal(nextMenuIndex("End", 0, 4), 3);
});

test("focus outside the menu enters at the matching end", () => {
  assert.equal(nextMenuIndex("ArrowDown", -1, 4), 0);
  assert.equal(nextMenuIndex("ArrowUp", -1, 4), 3);
});

test("other keys and empty menus are left alone", () => {
  assert.equal(nextMenuIndex("Enter", 1, 4), null);
  assert.equal(nextMenuIndex("ArrowDown", 0, 0), null);
});
