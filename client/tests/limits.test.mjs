// Codex (#249): the client's batch size is the server's own limit.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { MAX_SONGS_PER_REQUEST, batches } from "../src/limits.ts";

test("one request carries at most the server's Tracks limit", () => {
  const spec = JSON.parse(
    readFileSync(new URL("../../api/openapi.v1.json", import.meta.url), "utf8"),
  );
  assert.equal(
    spec.components.schemas.Tracks.properties.tracks.maxItems,
    MAX_SONGS_PER_REQUEST,
  );
});

test("batches keep order and size", () => {
  assert.deepEqual(batches([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]);
  assert.deepEqual(batches([], 2), []);
});
