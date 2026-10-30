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

/** A tenth of a second of silence, so the audio element loads without error. */
function silence() {
  const samples = 800;
  const wav = new Uint8Array(44 + samples).fill(128);
  const view = new DataView(wav.buffer);
  const text = (at: number, value: string) =>
    [...value].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples, true);
  text(8, "WAVEfmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 8000, true);
  view.setUint32(28, 8000, true);
  view.setUint16(32, 1, true);
  view.setUint16(34, 8, true);
  text(36, "data");
  view.setUint32(40, samples, true);
  return "data:audio/wav;base64," + Buffer.from(wav).toString("base64");
}

test("a skipped song's late start failure doesn't cover the next song", async ({
  page,
}) => {
  // The browser refuses the first song's play() only after the skip.
  await page.addInitScript(() => {
    let calls = 0;
    HTMLMediaElement.prototype.play = function () {
      calls += 1;
      if (calls > 1) return Promise.resolve();
      return new Promise((_, reject) =>
        setTimeout(
          () => reject(new DOMException("blocked", "NotAllowedError")),
          600,
        ),
      );
    };
  });
  const url = silence();
  await mockApi(page, {
    answers: {
      "/v1/playback/t1/url": (route) =>
        route.fulfill({ json: { url, expires_in: 60 } }),
      "/v1/playback/t2/url": (route) =>
        route.fulfill({ json: { url, expires_in: 60 } }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  const mini = page.locator(".mini-player");
  await expect(mini).toContainText("Von dutch");
  await mini.getByRole("button", { name: "Next song" }).click();
  await expect(mini).toContainText("360");
  await page.waitForTimeout(1000);
  await expect(mini).not.toContainText("Tap Play");
  await expect(mini).toContainText("Charli xcx");
});
