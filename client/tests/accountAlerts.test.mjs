import assert from "node:assert/strict";
import test from "node:test";

const { guessedAccounts } = await import("../src/accountAlerts.ts");

test("the People page warns about accounts being guessed this week (D-49)", () => {
  const now = 1_800_000_000;
  const events = [
    { at: now - 60, display_name: "Mark", event: "sign_in_slowed" },
    { at: now - 120, display_name: "Mark", event: "sign_in_slowed" },
    { at: now - 3600, display_name: "Sam", event: "login_failed" },
    { at: now - 8 * 86400, display_name: "Sam", event: "sign_in_slowed" },
    { at: now - 600, event: "sign_in_slowed" },
  ];
  assert.deepEqual(guessedAccounts(events, now), [
    "Mark",
    "an account that no longer exists",
  ]);
  assert.deepEqual(guessedAccounts([], now), []);
});
