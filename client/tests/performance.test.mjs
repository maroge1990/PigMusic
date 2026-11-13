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

const { performanceRows, shown, sourceLines, verdict } = await import(
  "../src/performance.ts"
);

const report = {
  targets: {
    taps_played: 0.9,
    first_sound_median_ms: 1500,
    first_sound_p90_ms: 3000,
    mix_downloads_completed: 0.8,
    library_downloads_completed: 0.95,
    artwork_placeholders: 0.02,
    artists_with_photo: 0.95,
  },
  instant: { taps: 34, played: 11, played_share: 0.3235, server_answer: {} },
  first_sound: { count: 20, median_ms: 1200, p90_ms: 4500 },
  downloads: {
    mix: { completed: 263, failed: 290, cancelled: 4, completion: 0.4756 },
    library: { completed: 0, failed: 0, cancelled: 0, completion: null },
  },
  artwork: { distinct: 100, placeholders: 1, placeholder_share: 0.01 },
  artists: null,
};

test("each parity number is judged against its target (#191)", () => {
  const rows = Object.fromEntries(
    performanceRows(report).map((row) => [row.name, row]),
  );
  assert.equal(verdict(rows["Taps that play"]), "Below target");
  assert.equal(rows["Taps that play"].basis, "11 of 34 taps");
  assert.equal(verdict(rows["First sound, median"]), "Meets target");
  assert.equal(verdict(rows["First sound, slowest tenth"]), "Below target");
  assert.equal(rows["Mix downloads completed"].basis, "263 of 553");
  assert.equal(verdict(rows["Covers left as placeholders"]), "Meets target");
  // No data is not a verdict.
  assert.equal(verdict(rows["Library requests completed"]), "");
  assert.equal(verdict(rows["Library artists with a photo"]), "");
  assert.equal(shown(0.3235), "32.4%");
  assert.equal(shown(1200, true), "1.2 s");
  assert.equal(shown(null), "No data yet");
});

test("each instant source shows the taps it played and how it answered", () => {
  const lines = sourceLines({
    ...report,
    instant: {
      ...report.instant,
      by_source: { youtube: 4, jiosaavn: 7 },
      sources: {
        youtube: {
          outcomes: { found: 4, "no match": 2, refused: 1 },
          count: 4,
          median_ms: 2100,
          p90_ms: 3000,
        },
        jiosaavn: {
          outcomes: { found: 7, "no match": 6 },
          count: 7,
          median_ms: 1400,
          p90_ms: 1900,
        },
      },
    },
  });
  assert.deepEqual(lines, [
    "JioSaavn: played 7 · found 7 · no match 6 · found in 1.4 s (median)",
    "YouTube: played 4 · found 4 · no match 2 · refused 1 · found in 2.1 s (median)",
  ]);
  assert.deepEqual(sourceLines(report), []);
});
