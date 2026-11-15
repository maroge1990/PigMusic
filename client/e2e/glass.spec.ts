import { expect, test, type Locator, type Page } from "@playwright/test";
import { album, mockApi } from "./mock";

// Brand glass (D-85, #179): the song menu and the phone dock use the vendored
// .pig-glass; the dark player keeps its own translucency. Every frosted surface
// turns solid when the system asks for reduced transparency.

const style = (el: Locator) =>
  el.evaluate((node) => {
    const css = getComputedStyle(node);
    return {
      backdrop: css.backdropFilter,
      background: css.backgroundColor,
    };
  });

async function reduceTransparency(page: Page) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setEmulatedMedia", {
    features: [{ name: "prefers-reduced-transparency", value: "reduce" }],
  });
}

// Chromium prints color-mix() results as color(srgb r g b / alpha).
const opaque = (color: string) => !/(,|\/)\s*0?\.\d+\)$/.test(color);
const alpha = (color: string) =>
  Number(/(?:,|\/)\s*([\d.]+)\)$/.exec(color)?.[1] ?? 1);

async function playAndOpenMenu(page: Page) {
  await mockApi(page);
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  await expect(page.locator(".mini-player")).toBeVisible();
  await page
    .getByRole("button", { name: "More options for Von dutch" })
    .first()
    .click();
  const menu = page.getByRole("menu", { name: "Von dutch" });
  await expect(menu).toBeVisible();
  return menu;
}

test("the song menu and phone dock are brand glass", async ({ page }, info) => {
  const menu = await playAndOpenMenu(page);
  await expect(menu).toHaveClass(/\bpig-glass\b/);
  expect((await style(menu)).backdrop).toContain("blur(10px)");
  const nav = page.locator(".personal-header .app-nav");
  if (info.project.name === "phone") {
    expect((await style(nav)).backdrop).toContain("blur(10px)");
  } else {
    // The player keeps its own glass, which the build used to strip.
    expect((await style(page.locator(".mini-player"))).backdrop).toContain(
      "blur(24px)",
    );
    // The desktop header nav is not a floating control.
    expect(await style(nav)).toEqual({
      backdrop: "none",
      background: "rgba(0, 0, 0, 0)",
    });
  }
});

test("reduced transparency makes every frosted surface solid", async ({
  page,
}, info) => {
  await reduceTransparency(page);
  const menu = await playAndOpenMenu(page);
  const surfaces = [menu];
  if (info.project.name === "phone") {
    surfaces.push(page.locator(".personal-header .app-nav"));
  } else {
    surfaces.push(page.locator(".mini-player"));
  }
  for (const surface of surfaces) {
    const { backdrop, background } = await style(surface);
    expect(backdrop).toBe("none");
    expect(opaque(background), background).toBe(true);
  }
});

test("reduced transparency makes the modal scrim near-opaque", async ({
  page,
}) => {
  // #187: the scrim lost its blur but stayed ~73% black, so the page showed
  // through unblurred.
  const review = {
    ...album,
    tracks: [{ ...album.tracks[0], status: "review", selected: true }],
  };
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/migration": (route) =>
        route.fulfill({
          json: {
            source_name: "Library.xml",
            imported_at: "2026-10-21T00:00:00Z",
            albums: [review],
            stats: { tracks: 1, saved: 0, missing: 0, review: 1, selected: 1 },
          },
        }),
    },
  });
  await reduceTransparency(page);
  await page.goto("/#/admin/migration?imported=1");
  await page.getByRole("button", { name: "Import now" }).click();
  await expect(
    page.getByRole("dialog", { name: "Check these versions." }),
  ).toBeVisible();
  const { backdrop, background } = await style(page.locator(".modal-backdrop"));
  expect(backdrop).toBe("none");
  expect(alpha(background), background).toBeGreaterThanOrEqual(0.9);
});
