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
