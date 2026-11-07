import { expect, test, type Page, type Route } from "@playwright/test";
import { album, mockApi } from "./mock";

// Batch 4 (#150-#156): shared pieces look the same on every page.

const queue = {
  downloads_enabled: true,
  items: [{ id: "q1", title: "One", artist: "A", status: "queued" }],
};
const migration = {
  source_name: "Library.xml",
  imported_at: "2026-10-21T00:00:00Z",
  albums: [],
  stats: { tracks: 30, saved: 10, missing: 20, review: 0, selected: 0 },
};
const owner = (page: Page) =>
  mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/queue": (r: Route) => r.fulfill({ json: queue }),
      "/v1/admin/migration": (r: Route) => r.fulfill({ json: migration }),
    },
  });

/** The computed look of a page's title block. */
async function header(page: Page) {
  const h1 = page.locator("main h1").first();
  await expect(h1).toBeVisible();
  return h1.evaluate((el) => {
    const block = el.closest("header");
    const sub = block?.querySelector("h1 + p");
    const s = getComputedStyle(el);
    return {
      inPageHeader: !!block?.classList.contains("page-header"),
      size: s.fontSize,
      tracking: s.letterSpacing,
      after: block ? getComputedStyle(block).marginBottom : null,
      subtitle: sub ? getComputedStyle(sub).fontSize : null,
    };
  });
}

test("#150: every non-hero page has the same title block", async ({ page }) => {
  await owner(page);
  const looks: Record<string, unknown> = {};
  for (const path of [
    "/#/home",
    "/#/library",
    `/#/library/album/${album.id}`,
    "/#/account",
    "/#/admin",
  ]) {
    await page.goto(path);
    const look = await header(page);
    expect(look.inPageHeader, path).toBe(true);
    looks[path] = look;
  }
  const { size, ...first } = looks["/#/home"] as { size: string };
  for (const [path, look] of Object.entries(looks)) {
    const { size: own, ...rest } = look as { size: string };
    expect(rest, path).toEqual(first);
    // On phones a cover page's title steps down one size to fit beside it.
    if (!path.includes("album") || test.info().project.name === "desktop")
      expect(own, path).toBe(size);
  }
  await expect(page.locator(".page-heading")).toHaveCount(0);
});

test("#150: Admin's queue and import sections use the section header", async ({
  page,
}) => {
  await owner(page);
  for (const [path, title] of [
    ["/#/admin/downloads", "Download queue"],
    ["/#/admin/migration", "Whole-library import"],
  ]) {
    await page.goto(path);
    const h2 = page.getByRole("heading", { level: 2, name: title });
    await expect(h2).toBeVisible();
    expect(
      await h2.evaluate((el) => !!el.closest("header.section-title")),
    ).toBe(true);
  }
});

/** Height and corner radius of every visible button in each family. */
async function buttonLooks(page: Page) {
  return page.evaluate(() => {
    const families: Record<string, string> = {
      full: ".primary-button, .secondary-button, .personal-main form button:not([class]), .account-section button:not([class])",
      small: ".small-button",
      round: ".round-action",
    };
    const out: Record<string, string[]> = {};
    for (const [name, selector] of Object.entries(families))
      out[name] = [...document.querySelectorAll<HTMLElement>(selector)]
        .filter((el) => el.offsetParent)
        .map((el) => {
          const s = getComputedStyle(el);
          const radius =
            s.borderTopLeftRadius === s.borderBottomLeftRadius &&
            parseFloat(s.borderTopLeftRadius) >= el.offsetHeight / 2
              ? "pill"
              : s.borderTopLeftRadius;
          return `${Math.round(el.getBoundingClientRect().height)} ${radius}`;
        });
    return out;
  });
}

test("#152: each button family has one height and one shape", async ({
  page,
}) => {
  await owner(page);
  const seen: Record<string, Set<string>> = {
    full: new Set(),
    small: new Set(),
    round: new Set(),
  };
  for (const path of [
    `/#/library/album/${album.id}`,
    "/#/library/playlists",
    "/#/account",
    "/#/admin/downloads",
    "/#/admin/migration",
  ]) {
    await page.goto(path);
    await expect(page.locator("main h1").first()).toBeVisible();
    for (const [family, looks] of Object.entries(await buttonLooks(page)))
      for (const look of looks) seen[family].add(look);
  }
  // Every family is the 44px touch target; full and small are capsules.
  expect([...seen.full]).toEqual(["44 pill"]);
  expect([...seen.round]).toEqual(["44 pill"]);
  for (const look of seen.small) expect(look).toBe("44 pill");
});

/** The keyboard focus ring drawn on the element a Tab press reaches. */
async function tabRing(page: Page) {
  await page.keyboard.press("Tab");
  return page.evaluate(() => {
    const s = getComputedStyle(document.activeElement!);
    return `${s.outlineStyle} ${s.outlineWidth} ${s.outlineOffset}`;
  });
}

test("#152: secondary buttons answer a hover", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "hover needs a mouse");
  await owner(page);
  await page.goto("/#/admin/downloads");
  const clear = page.getByRole("button", { name: "Clear finished" });
  const fill = () =>
    clear.evaluate((el) => getComputedStyle(el).backgroundColor);
  const resting = await fill();
  await clear.hover();
  await expect.poll(fill).not.toBe(resting);
});

test("#152: sign-in and the app draw one focus ring", async ({ page }) => {
  await mockApi(page, {
    answers: {
      "/v1/me": (r: Route) =>
        r.fulfill({ status: 401, json: { detail: "Sign in", code: "auth" } }),
    },
  });
  await page.goto("/#/home");
  await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  const signIn = await tabRing(page);
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await owner(page);
  await page.goto("/#/home");
  await expect(page.locator("main h1").first()).toBeVisible();
  const app = await tabRing(page);
  expect(signIn).toBe("solid 2px 3px");
  expect(app).toBe(signIn);
});

const PANELS =
  ".system-panel, .account-section, .migration-summary, .owner-links";

test("#151: one section heading and one panel shape", async ({ page }) => {
  await owner(page);
  const headings = new Set<string>();
  const radii = new Set<string>();
  for (const path of [
    "/#/home",
    "/#/search?q=Charli",
    "/#/library/artist/artist-1",
    "/#/account",
    "/#/admin",
    "/#/admin/people",
    "/#/admin/migration",
  ]) {
    await page.goto(path);
    await expect(page.locator("main h1").first()).toBeVisible();
    await expect(page.locator("main h2").first()).toBeVisible();
    const found = await page.evaluate((panels) => {
      const loose: string[] = [];
      const looks: string[] = [];
      for (const h2 of document.querySelectorAll<HTMLElement>("main h2")) {
        if (h2.closest("dialog") || !h2.offsetParent) continue;
        const inPanel = h2.parentElement?.matches(panels);
        if (!inPanel && !h2.closest("header.section-title"))
          loose.push(h2.textContent || "");
        const s = getComputedStyle(h2);
        looks.push(`${s.fontSize} ${s.fontWeight} ${s.letterSpacing}`);
      }
      const radii = [...document.querySelectorAll<HTMLElement>(panels)]
        .filter((el) => el.offsetParent)
        .map((el) => getComputedStyle(el).borderTopLeftRadius);
      return { loose, looks, radii };
    }, PANELS);
    expect(found.loose, path).toEqual([]);
    found.looks.forEach((look) => headings.add(look));
    found.radii.forEach((radius) => radii.add(radius));
  }
  expect([...headings]).toHaveLength(1);
  expect([...radii]).toHaveLength(1);
});

test("#155: loading is announced, with one ellipsis", async ({ page }) => {
  await mockApi(page, {
    role: "owner",
    // The queue never answers, so its loading line stays.
    answers: { "/v1/admin/queue": () => new Promise<void>(() => {}) },
  });
  await page.goto("/#/admin/downloads");
  await expect(
    page.getByRole("status").filter({ hasText: "Loading requests" }),
  ).toHaveText("Loading requests…");
});

test("#155: empty lines look the same; errors use the inline error", async ({
  page,
}) => {
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/search": (r: Route) =>
        r.fulfill({ json: { tracks: [], household_tracks: [] } }),
      "/v1/catalogue/artists": (r: Route) =>
        r.fulfill({ json: { artists: [] } }),
      "/v1/admin/library/trash": (r: Route) => r.fulfill({ json: [] }),
    },
  });
  const colour = (text: string | RegExp) =>
    page.getByText(text).evaluate((el) => getComputedStyle(el).color);
  await page.goto("/#/search?q=Zzzz");
  const looks = [
    await colour("Nothing in your library yet."),
    await colour(/No catalogue artists match/),
  ];
  await page.goto("/#/admin/library");
  looks.push(await colour("No music is waiting for assignment."));
  expect(new Set(looks).size).toBe(1);

  // A failed search shows the shared inline error.
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await mockApi(page);
  await page.goto("/#/search?q=Charli");
  await expect(page.getByRole("alert").first()).toHaveClass(/inline-error/);
});
