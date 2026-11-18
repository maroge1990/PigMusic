// #250, D-79: one in-app confirmation for what can't be undone. Focus on
// Cancel and Escape are checked in a browser (e2e/confirm.spec.ts).
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

const {
  answerConfirm,
  askToConfirm,
  confirmSession,
  endConfirmSession,
  setConfirmIdentity,
  subscribeConfirm,
} = await import("../src/confirmDialog.ts");

const question = {
  title: "Disable Sam's account?",
  body: "Sam is signed out everywhere.",
  action: "Disable",
  destructive: true,
};

test("the action resolves true and Cancel false", async () => {
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  const yes = askToConfirm(question);
  assert.equal(open.title, "Disable Sam's account?");
  answerConfirm(open.id, true);
  assert.equal(await yes, true);
  assert.equal(open, null);
  const no = askToConfirm(question);
  answerConfirm(open.id, false);
  assert.equal(await no, false);
  stop();
});

test("a new question cancels one still open", async () => {
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  const first = askToConfirm(question);
  const second = askToConfirm({ ...question, title: "Reset Sam's password?" });
  assert.equal(await first, false);
  answerConfirm(open.id, true);
  assert.equal(await second, true);
  stop();
});

test("a stale handler can't answer a newer question (Codex round 1)", async () => {
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  const first = askToConfirm(question);
  const firstId = open.id;
  const second = askToConfirm({ ...question, title: "Reset Sam's password?" });
  assert.equal(await first, false);
  // The first question's buttons, still on screen for a moment, answer.
  answerConfirm(firstId, true);
  assert.equal(open?.title, "Reset Sam's password?");
  let settled = false;
  void second.then(() => (settled = true));
  await new Promise((r) => setImmediate(r));
  assert.equal(settled, false);
  answerConfirm(open.id, false);
  assert.equal(await second, false);
  stop();
});

test("losing the session cancels an open question (Codex round 1)", async () => {
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  const asked = askToConfirm(question);
  const id = open.id;
  endConfirmSession();
  assert.equal(await asked, false);
  assert.equal(open, null);
  // Its buttons can't bring it back for the next account.
  answerConfirm(id, true);
  assert.equal(open, null);
  stop();
});

test("a question asked for an ended session is never shown", async () => {
  const session = confirmSession();
  endConfirmSession();
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  assert.equal(await askToConfirm(question, session), false);
  assert.equal(open, null);
  stop();
});

test("a confirmed question checks the server still has the asking session (Codex)", async () => {
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  // Another tab signed in as Sam, replacing the shared cookie.
  let server = "s-pat";
  setConfirmIdentity("s-pat", async () => server);
  const asked = askToConfirm(question);
  server = "s-sam";
  answerConfirm(open.id, true);
  assert.equal(await asked, false);
  // Still Pat's session: the action goes ahead.
  server = "s-pat";
  const again = askToConfirm(question);
  answerConfirm(open.id, true);
  assert.equal(await again, true);
  // The check couldn't be made: nothing is done.
  setConfirmIdentity("s-pat", async () => {
    throw new Error("offline");
  });
  const offline = askToConfirm(question);
  answerConfirm(open.id, true);
  assert.equal(await offline, false);
  setConfirmIdentity(null, null);
  stop();
});

test("no page uses the browser's own confirm dialog", () => {
  const src = new URL("../src/", import.meta.url);
  for (const file of readdirSync(src).filter((f) => /\.tsx?$/.test(f)))
    assert.doesNotMatch(
      readFileSync(new URL(file, src), "utf8"),
      /(?<![\w.])(window\.)?confirm\(/,
      `${file} calls confirm()`,
    );
});
