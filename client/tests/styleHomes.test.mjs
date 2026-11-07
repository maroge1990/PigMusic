// #156: each selector has one home, every styled class is rendered, colours
// come from tokens, and the stylesheet is imported once.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";

const SRC = new URL("../src/", import.meta.url);
const read = (name) => readFileSync(new URL(name, SRC), "utf8");
const sheets = readdirSync(SRC)
  .filter((name) => name.endsWith(".css") && name !== "tokens.css")
  .map((name) => [name, read(name).replace(/\/\*[\s\S]*?\*\//g, "")]);
const code = readdirSync(SRC)
  .filter((name) => /\.tsx?$/.test(name))
  .map(read)
  .join("\n");

/** Each style rule's selectors with the at-rule they sit in. */
function rules(css) {
  const found = [];
  const stack = [];
  for (const m of css.matchAll(/([^{}]*)\{|\}/g)) {
    if (m[0] === "}") {
      stack.pop();
      continue;
    }
    const head = m[1].trim().replace(/\s+/g, " ");
    if (head.startsWith("@")) stack.push(head);
    else {
      const media = stack.filter((h) => h?.startsWith("@")).at(-1) || "";
      for (const selector of head.split(","))
        found.push([selector.trim(), media]);
      stack.push(null);
    }
  }
  return found;
}

test("a selector is styled in one file only", () => {
  const homes = new Map();
  for (const [name, css] of sheets)
    for (const [selector, media] of rules(css)) {
      if (selector === ":root") continue; // each sheet's own custom properties
      const key = `${media} ${selector}`;
      homes.set(key, new Set([...(homes.get(key) || []), name]));
    }
  const shared = [...homes]
    .filter(([, files]) => files.size > 1)
    .map(([key, files]) => `${key}: ${[...files].join(", ")}`);
  assert.deepEqual(shared, []);
});

test("every class the CSS styles is rendered somewhere", () => {
  const styled = new Set();
  for (const [, css] of sheets)
    for (const [selector] of rules(css))
      for (const m of selector.matchAll(/\.([a-zA-Z][\w-]*)/g))
        styled.add(m[1]);
  const unused = [...styled].filter(
    (name) => !new RegExp(`(?<![\\w-])${name}(?![\\w-])`).test(code),
  );
  assert.deepEqual(unused, []);
});

test("backdrops, shadows and media text use tokens, not raw black or white", () => {
  const raw = [];
  // A mask's colour is only its alpha, so a black mask stop is not paint.
  for (const [name, css] of sheets)
    for (const m of css.matchAll(/([a-z-]+)\s*:\s*([^;{}]+);/g))
      if (
        !m[1].startsWith("mask") &&
        /#000\b|#0004\b|#0008\b|#fff\b|rgb\(0 0 0/.test(m[2])
      )
        raw.push(`${name} ${m[1]}: ${m[2]}`);
  assert.deepEqual(raw, []);
});

test("styles.css is imported once, by the entry point", () => {
  const importers = readdirSync(SRC).filter(
    (name) =>
      /\.tsx?$/.test(name) && /import "\.\/styles\.css"/.test(read(name)),
  );
  assert.deepEqual(importers, ["main.tsx"]);
});
