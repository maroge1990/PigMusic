// #250, D-79: one in-app confirmation for what can't be undone. Focus on
// Cancel and Escape are checked in a browser (e2e/confirm.spec.ts).
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

const { answerConfirm, askToConfirm, subscribeConfirm } = await import(
  "../src/confirmDialog.ts"
);

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
  answerConfirm(true);
  assert.equal(await yes, true);
  assert.equal(open, null);
  const no = askToConfirm(question);
  answerConfirm(false);
  assert.equal(await no, false);
  stop();
});

test("a new question cancels one still open", async () => {
  const first = askToConfirm(question);
  const second = askToConfirm({ ...question, title: "Reset Sam's password?" });
  assert.equal(await first, false);
  answerConfirm(true);
  assert.equal(await second, true);
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
