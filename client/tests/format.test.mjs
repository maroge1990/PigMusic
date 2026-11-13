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
const {
  formatDate,
  formatCount,
  formatRelative,
  formatDateTime,
  formatDuration,
} = await import("../src/format.ts");

test("MusicBrainz partial dates keep only what they know", () => {
  assert.equal(formatDate("2010-07-03"), "3 Jul 2010");
  assert.equal(formatDate("2010-07"), "Jul 2010");
  assert.equal(formatDate("2010"), "2010");
  assert.equal(formatDate(""), "");
  assert.equal(formatDate(undefined), "");
  assert.equal(formatDate("2010-13"), "2010");
});

test("counts use en-AU grouping", () => {
  assert.equal(formatCount(1408), "1,408");
  assert.equal(formatCount(12), "12");
  assert.equal(formatCount(undefined), "0");
});

test("relative times read naturally and fall back to a date", () => {
  const now = Date.parse("2026-10-09T05:00:00Z");
  const ago = (seconds) => new Date(now - seconds * 1000).toISOString();
  assert.equal(formatRelative(ago(10), now), "just now");
  assert.equal(formatRelative(ago(26 * 60), now), "26 min ago");
  assert.equal(formatRelative(ago(3 * 3600), now), "3 h ago");
  assert.equal(formatRelative(ago(30 * 3600), now), "yesterday");
  assert.equal(formatRelative((now - 26 * 60000) / 1000, now), "26 min ago");
  assert.equal(
    formatRelative(ago(5 * 86400), now),
    formatDateTime(ago(5 * 86400)),
  );
  assert.equal(formatRelative("nonsense", now), "");
});

test("date and time is one medium en-AU format", () => {
  assert.match(formatDateTime("2026-10-09T12:06:00Z"), /^9 Oct\w* 2026, /);
  assert.equal(formatDateTime("nonsense"), "");
});

test("durations floor whole seconds and never show :60", () => {
  assert.equal(formatDuration(59.6), "0:59");
  assert.equal(formatDuration(119.5), "1:59");
  assert.equal(formatDuration(187), "3:07");
  assert.equal(formatDuration(3600), "60:00");
  assert.equal(formatDuration(undefined), "");
  assert.equal(formatDuration(0), "");
  assert.equal(formatDuration(0, "0:00"), "0:00");
  assert.equal(formatDuration(Number.NaN, "0:00"), "0:00");
});

test("every duration on screen comes from formatDuration", async () => {
  const { readFile, readdir } = await import("node:fs/promises");
  const dir = new URL("../src/", import.meta.url);
  for (const name of await readdir(dir)) {
    if (!/\.tsx?$/.test(name) || name === "format.ts") continue;
    const text = await readFile(new URL(name, dir), "utf8");
    assert.doesNotMatch(
      text,
      /% 60\)/,
      `${name} formats a duration by hand; use formatDuration`,
    );
  }
});
