// #250, D-79: one in-app confirmation for what can't be undone. Focus on
// Cancel and Escape are checked in a browser (e2e/confirm.spec.ts).
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

const { setSessionId } = await import("../src/sessionId.ts");
const { answerConfirm, askToConfirm, endConfirmSession, subscribeConfirm } =
  await import("../src/confirmDialog.ts");
const { clearNotices, subscribe } = await import("../src/notices.ts");

const question = {
  title: "Disable Sam's account?",
  body: "Sam is signed out everywhere.",
  action: "Disable",
  destructive: true,
};

test.beforeEach(() => setSessionId("s-pat"));

test("the action resolves with the asking session, Cancel with null", async () => {
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  const yes = askToConfirm(question);
  assert.equal(open.title, "Disable Sam's account?");
  answerConfirm(open.id, true);
  // The write names this session, so the server refuses it under another.
  assert.equal(await yes, "s-pat");
  assert.equal(open, null);
  const no = askToConfirm(question);
  answerConfirm(open.id, false);
  assert.equal(await no, null);
  stop();
});

test("a new question cancels one still open", async () => {
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  const first = askToConfirm(question);
  const second = askToConfirm({ ...question, title: "Reset Sam's password?" });
  assert.equal(await first, null);
  answerConfirm(open.id, true);
  assert.equal(await second, "s-pat");
  stop();
});

test("a stale handler can't answer a newer question (Codex round 1)", async () => {
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  const first = askToConfirm(question);
  const firstId = open.id;
  const second = askToConfirm({ ...question, title: "Reset Sam's password?" });
  assert.equal(await first, null);
  // The first question's buttons, still on screen for a moment, answer.
  answerConfirm(firstId, true);
  assert.equal(open?.title, "Reset Sam's password?");
  let settled = false;
  void second.then(() => (settled = true));
  await new Promise((r) => setImmediate(r));
  assert.equal(settled, false);
  answerConfirm(open.id, false);
  assert.equal(await second, null);
  stop();
});

test("losing the session cancels an open question (Codex round 1)", async () => {
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  const asked = askToConfirm(question);
  const id = open.id;
  endConfirmSession();
  assert.equal(await asked, null);
  assert.equal(open, null);
  // Its buttons can't bring it back for the next account.
  answerConfirm(id, true);
  assert.equal(open, null);
  stop();
});

test("a question started under a session that has changed is never shown", async () => {
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  const started = "s-pat";
  setSessionId("s-sam");
  assert.equal(await askToConfirm(question, started), null);
  assert.equal(open, null);
  stop();
});

test("with no session known, nothing is asked and it says why (Codex round 2)", async () => {
  // Between losing a session and /v1/me answering again.
  setSessionId(null);
  clearNotices();
  let notices = [];
  const stopNotices = subscribe((all) => (notices = all));
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  assert.equal(await askToConfirm(question), null);
  assert.equal(open, null);
  assert.deepEqual(
    notices.map((n) => [n.kind, n.text]),
    [
      [
        "error",
        "Couldn't tell who is signed in. Reload PigMusic and try again.",
      ],
    ],
  );
  stop();
  stopNotices();
  clearNotices();
});

test("a question answered after the session changed resolves null", async () => {
  let open = null;
  const stop = subscribeConfirm((q) => (open = q));
  const asked = askToConfirm(question);
  setSessionId("s-sam");
  answerConfirm(open.id, true);
  assert.equal(await asked, null);
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
