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
