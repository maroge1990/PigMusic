// #153: the web client's CSS uses the shared scales, not per-file values.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";

const SRC = new URL("../src/", import.meta.url);
const files = readdirSync(SRC)
  .filter((name) => name.endsWith(".css") && name !== "tokens.css")
  .map((name) => [name, readFileSync(new URL(name, SRC), "utf8")]);

/** Every `property: value` declaration, with its file. */
function declarations(property) {
  const found = [];
  for (const [name, css] of files)
    for (const m of css.matchAll(/(?:^|[{;\s])([a-z-]+)\s*:\s*([^;{}]+);/g))
      if (property.test(m[1])) found.push([name, m[1], m[2].trim()]);
  return found;
}

test("spacing uses the seven --space tokens (hairlines under 3px aside)", () => {
  // Kept on purpose: the artist photo's top offset and the song error's
  // indent, which follows the 3.75rem cover column, not the spacing scale.
  const allowed = new Set(["200px", "240px", "3.75rem", "4rem", "6rem"]);
  const raw = [];
  for (const [name, prop, value] of declarations(
    /^(padding|margin|gap|row-gap|column-gap)(-[a-z-]+)?$/,
  ))
    for (const m of value.matchAll(/(?<![\w.-])-?(\d*\.?\d+)(px|rem)\b/g)) {
      const px = Number(m[1]) * (m[2] === "rem" ? 16 : 1);
      if (px >= 3 && !allowed.has(m[0].replace("-", "")))
        raw.push(`${name} ${prop}: ${value}`);
    }
  assert.deepEqual(raw, []);
});

test("one emphasis weight and one heading weight", () => {
  const weights = new Set(
    declarations(/^font-weight$/).map(([, , value]) => value),
  );
  assert.deepEqual([...weights].sort(), ["400", "600", "650"]);
});

test("body copy has one line height", () => {
  const loose = declarations(/^line-height$/).filter(([, , value]) =>
    /^1\.[6-9]|px$/.test(value),
  );
  assert.deepEqual(loose, []);
});

test("shadows use the four recipes", () => {
  const shadows = declarations(/^box-shadow$/).filter(
    ([, , value]) =>
      !/^var\(--elevation-(soft|card|floating|dialog)\)$/.test(value) &&
      value !== "none" &&
      !value.startsWith("inset"),
  );
  assert.deepEqual(shadows, []);
});

test("#182: letter-spacing scales with the type (em, not px)", () => {
  const fixed = declarations(/^letter-spacing$/).filter(([, , value]) =>
    /px\b/.test(value),
  );
  assert.deepEqual(fixed, []);
});

test("#182 (Codex round 2): the shelf fade applies only where scroll timelines exist", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("Experience.css", SRC), "utf8");
  const guarded =
    /@supports \(animation-timeline: scroll\(x self\)\) \{\s*\.home-songs \{[^}]*\}\s*\}/.exec(
      css,
    );
  assert.ok(guarded, "the fade sits in an @supports block");
  const rest = css.replace(guarded[0], "");
  for (const rule of rest.matchAll(/\.home-songs \{([^}]*)\}/g))
    assert.doesNotMatch(rule[1], /mask-image|animation/);
});
