import assert from "node:assert/strict";
import test from "node:test";

const { StartClock, startSource } = await import("../src/firstSound.ts");

test("first sound is timed once per start, for the newest start only (#191)", () => {
  const clock = new StartClock();
  clock.begin(1, "play", 1000);
  clock.begin(2, "skip", 1500); // A skip before the first song was heard.
  assert.equal(clock.heard(1, 1600), null);
  assert.deepEqual(clock.heard(2, 2700.4), { ms: 1200, action: "skip" });
  assert.equal(clock.heard(2, 3000), null); // A pause and resume is not a start.
  clock.begin(3, "previous", 4000);
  clock.cancel(3);
  assert.equal(clock.heard(3, 4500), null);
});

test("player source labels map to the server's kinds (#191)", () => {
  assert.equal(startSource("Saved library"), "library");
  assert.equal(startSource("Your mix"), "mix");
  assert.equal(startSource("Prepared mix"), "mix");
  assert.equal(startSource("Remote · JioSaavn"), "remote");
  assert.equal(startSource(undefined), "other");
});
