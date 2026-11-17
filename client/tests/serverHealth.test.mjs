// #241: System says what broke and why, in plain words.
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
  busyLine,
  noProblems,
  restartsLine,
  workerName,
  workerNote,
  workerState,
} = await import("../src/serverHealth.ts");

const quiet = {
  started_at: 1_700_000_000,
  previous_run: null,
  restarts_last_day: 0,
  workers: [],
  recent_errors: [],
  busy: { count: 0, last_at: null },
  startup_problems: [],
};

test("a quiet server has nothing to report", () => {
  assert.equal(noProblems(quiet), true);
  assert.equal(busyLine(quiet), "");
  assert.equal(restartsLine(quiet), "");
});

test("errors, busy answers and set-aside tasks are problems", () => {
  const error = {
    at: 1,
    request_id: "abc",
    method: "GET",
    path: "/api/v1/x",
    error: "RuntimeError",
    sentence:
      "Something went wrong on the server while handling GET /api/v1/x.",
  };
  assert.equal(noProblems({ ...quiet, recent_errors: [error] }), false);
  assert.equal(
    noProblems({
      ...quiet,
      startup_problems: [{ at: 1, area: "tidy", sentence: "…" }],
    }),
    false,
  );
  const busy = { ...quiet, busy: { count: 3, last_at: 5 } };
  assert.equal(noProblems(busy), false);
  assert.equal(
    busyLine(busy),
    "The database was too busy to answer 3 times since the server started.",
  );
  assert.equal(
    restartsLine({ ...quiet, restarts_last_day: 1 }),
    "The server had to restart once in the last day.",
  );
});

test("each worker reads as running, restarting or not connected", () => {
  const failing = {
    name: "downloads",
    running: false,
    restarts: 2,
    last_error: "OperationalError",
    last_error_at: null,
  };
  assert.equal(workerName("downloads"), "Downloads");
  assert.equal(workerName("source engine"), "Soulseek engine");
  assert.equal(workerState(failing), "Restarting");
  assert.equal(workerState({ ...failing, running: true }), "Running");
  assert.equal(
    workerState({ name: "source engine", running: false }),
    "Not connected",
  );
  assert.equal(
    workerNote(failing),
    "restarted 2 times since the server started",
  );
  // The error's class name is never shown to the household.
  assert.ok(!workerNote(failing).includes("OperationalError"));
  assert.equal(workerNote({ name: "exports", running: true, restarts: 0 }), "");
});
