// Codex round 2 on #267: a card reads its album again only when its own
// songs' requests changed state, never for download progress.
import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = { addEventListener() {}, dispatchEvent() {} };
const { queueSignature } = await import("../src/queueFeed.ts");

const songs = [
  { title: "One", release_id: "r", recording_id: "rec-1" },
  { title: "Two", release_id: "r" },
];
const item = (id, status, extra = {}) => ({ id, status, ...extra });

test("only these songs' requests, by id and state", () => {
  const mine = item("j1", "downloading", {
    musicbrainz_recording_id: "rec-1",
    received_bytes: 10,
  });
  const byName = item("j2", "waiting", {
    musicbrainz_release_id: "r",
    title: "two",
  });
  const other = item("x", "downloading", { musicbrainz_recording_id: "rec-9" });
  const before = queueSignature(songs, [mine, byName, other]);
  assert.equal(before, "j1:downloading,j2:waiting");
  // Progress and other songs change nothing.
  assert.equal(
    queueSignature(songs, [
      { ...mine, received_bytes: 90 },
      byName,
      { ...other, status: "failed" },
    ]),
    before,
  );
  // A state change, or a request gone, does.
  assert.notEqual(
    queueSignature(songs, [{ ...mine, status: "paused" }, byName]),
    before,
  );
  assert.notEqual(queueSignature(songs, [byName]), before);
  assert.equal(queueSignature(songs, null), "");
});
