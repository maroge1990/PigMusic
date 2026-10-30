import { expect, test } from "@playwright/test";
import { album, mockApi } from "./mock";

test("Now Playing keeps keyboard focus inside and returns it on close", async ({
  page,
}, info) => {
  test.skip(info.project.name === "phone", "Keyboard journey");
  await mockApi(page);
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  const open = page.locator(".mini-player").getByRole("button").first();
  await expect(page.locator(".mini-player")).toBeVisible();
  await open.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Now playing" });
  await expect(dialog).toBeVisible();
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press("Tab");
    expect(
      await dialog.evaluate((el) => el.contains(document.activeElement)),
    ).toBe(true);
  }
  await page.keyboard.press("Shift+Tab");
  expect(
    await dialog.evaluate((el) => el.contains(document.activeElement)),
  ).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(open).toBeFocused();
});

test("repeat cycles off, all and one, and is remembered", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  await page.locator(".mini-player").getByRole("button").first().click();
  const repeat = page.getByRole("button", { name: /^Repeat/ });
  await expect(repeat).toHaveAccessibleName("Repeat off");
  await repeat.click();
  await expect(repeat).toHaveAccessibleName("Repeat all");
  await repeat.click();
  await expect(repeat).toHaveAccessibleName("Repeat this song");
  expect(
    await page.evaluate(() => localStorage.getItem("pig-music-repeat")),
  ).toBe("one");
});

test("the player's artist link opens the library artist for library playback", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  await page.locator(".mini-player").getByRole("button").first().click();
  const dialog = page.getByRole("dialog", { name: "Now playing" });
  await dialog.getByRole("button", { name: "Charli xcx", exact: true }).click();
  await expect(page).toHaveURL(/#\/library\/artist\/artist-1$/);
});

test("the mini player shows the artist, not a status, while a song loads", async ({
  page,
}) => {
  let release = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  await mockApi(page, {
    answers: {
      "/v1/playback/t1/url": async (route) => {
        await held;
        await route.fulfill({
          status: 404,
          json: { detail: "File unavailable" },
        });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  const mini = page.locator(".mini-player");
  await expect(
    mini.getByRole("button", { name: "Play", exact: true }),
  ).toBeDisabled();
  await expect(mini).toContainText("Charli xcx");
  await expect(mini).not.toContainText("…");
  release();
  // A real problem still gets a short message.
  await expect(mini).toContainText("File unavailable");
});
