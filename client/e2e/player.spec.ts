import { expect, test, type Page, type Route } from "@playwright/test";
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

/** The mock album with a third song, each with playable audio. */
async function albumWithAudio(
  page: Page,
  answers: Record<string, (route: Route) => Promise<void>> = {},
) {
  const url = silence();
  const tracks = [
    ...album.tracks,
    { ...album.tracks[0], id: "t3", title: "Club classics", track_number: 3 },
  ];
  const audio: Record<string, (route: Route) => Promise<void>> = {};
  for (const t of tracks)
    audio[`/v1/playback/${t.id}/url`] = (route) =>
      route.fulfill({ json: { url, expires_in: 60 } });
  return mockApi(page, {
    answers: {
      "/v1/albums/album-1": (route) =>
        route.fulfill({ json: { ...album, track_count: 3, tracks } }),
      ...audio,
      ...answers,
    },
  });
}
const savedQueue = (page: Page) =>
  page.evaluate(() => JSON.parse(localStorage.getItem("pig-music-queue")!));

// #97: a song that plays to its end carries on to the next one.
test("a finished song plays the next one by itself", async ({ page }) => {
  const loaded: string[] = [];
  page.on("request", (request) => {
    const found = /\/playback\/(t\d)\/url$/.exec(request.url());
    if (found) loaded.push(found[1]);
  });
  await albumWithAudio(page);
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  const mini = page.locator(".mini-player");
  // A tenth of a second of audio ends at once, so the queue moves on
  // through every song in order.
  await expect(mini).toContainText("Club classics");
  expect(loaded).toEqual(["t1", "t2", "t3"]);
  // The last song ends the queue rather than wrapping (repeat is off).
  await expect(mini.getByRole("button", { name: "Next song" })).toBeDisabled();
});

// #97: a song that can't be played says why, and Next still moves on.
test("a song that fails to load shows why and Next skips it", async ({
  page,
}) => {
  await albumWithAudio(page, {
    "/v1/playback/t1/url": (route) =>
      route.fulfill({ status: 404, json: { detail: "File unavailable" } }),
  });
  await page.addInitScript(() => {
    // Keep each song playing so the test sees where the queue stands.
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  const mini = page.locator(".mini-player");
  await expect(mini).toContainText("File unavailable");
  await mini.getByRole("button", { name: "Next song" }).click();
  await expect(mini).toContainText("360");
  await expect(mini).not.toContainText("File unavailable");
});

// #97: Up next reorders and removes songs; the current one stays put.
test("Up next moves and removes songs, and skipping follows it", async ({
  page,
}) => {
  await albumWithAudio(page);
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  const mini = page.locator(".mini-player");
  await mini.getByRole("button").first().click();
  const upNext = page.getByRole("region", { name: "Up next" });
  const rows = upNext.locator("li strong");
  await expect(rows).toHaveText(["360", "Club classics"]);
  await expect(
    upNext.getByRole("button", { name: "Move 360 up" }),
  ).toBeDisabled();
  await upNext.getByRole("button", { name: "Move Club classics up" }).click();
  await expect(rows).toHaveText(["Club classics", "360"]);
  await upNext
    .getByRole("button", { name: "Remove Club classics from Up next" })
    .click();
  await expect(rows).toHaveText(["360"]);
  await expect
    .poll(async () => (await savedQueue(page))?.songs.map((s: any) => s.title))
    .toEqual(["Von dutch", "360"]);
  const dialog = page.getByRole("dialog", { name: "Now playing" });
  await dialog.getByRole("button", { name: "Next song" }).click();
  await expect(dialog.getByRole("heading", { level: 2 })).toHaveText("360");
  await expect(upNext).toHaveCount(0);
});

// #97: like iPhone, the queue survives a reload and comes back paused.
test("the queue is restored paused after a reload", async ({ page }) => {
  await albumWithAudio(page);
  await page.addInitScript(() => {
    (window as any).plays = 0;
    HTMLMediaElement.prototype.play = () => {
      (window as any).plays += 1;
      return Promise.resolve();
    };
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("360").first().click();
  const mini = page.locator(".mini-player");
  await expect(mini).toContainText("360");
  await expect.poll(async () => (await savedQueue(page))?.cursor).toBe(1);
  await page.reload();
  await expect(mini).toContainText("360");
  await page.waitForTimeout(300);
  expect(await page.evaluate(() => (window as any).plays)).toBe(0);
  await expect(
    mini.getByRole("button", { name: "Play", exact: true }),
  ).toBeEnabled();
  // Play loads the restored song, and the queue around it is intact.
  await mini.getByRole("button", { name: "Play", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).plays)).toBe(1);
  await expect(
    mini.getByRole("button", { name: "Previous song" }),
  ).toBeEnabled();
  await expect(mini.getByRole("button", { name: "Next song" })).toBeEnabled();
});

// #97: signing out stops the music and forgets the queue on this device.
test("signing out clears the player and its saved queue", async ({ page }) => {
  let signedIn = true;
  await albumWithAudio(page, {
    "/v1/auth/logout": (route) => {
      signedIn = false;
      return route.fulfill({ json: {} });
    },
    "/v1/me": (route) =>
      signedIn
        ? route.fulfill({
            json: {
              id: "u1",
              username: "pat",
              display_name: "Pat",
              role: "member",
              session_id: "session-1",
            },
          })
        : route.fulfill({
            status: 401,
            json: {
              detail: "Please sign in to continue.",
              code: "authentication_required",
            },
          }),
  });
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  const mini = page.locator(".mini-player");
  await expect(mini).toContainText("Von dutch");
  await expect.poll(async () => (await savedQueue(page))?.cursor).toBe(0);
  await page.goto("/#/account");
  await page.getByRole("button", { name: "Sign out of PigMusic" }).click();
  await expect(page.getByLabel("Username")).toBeVisible();
  await expect(mini).toHaveCount(0);
  expect(
    await page.evaluate(() => localStorage.getItem("pig-music-queue")),
  ).toBeNull();
  // A reload finds nothing to restore.
  await page.reload();
  await expect(page.getByLabel("Username")).toBeVisible();
  await expect(mini).toHaveCount(0);
});

test("#182: closing Now Playing by hand plays its exit; Reduce Motion closes at once", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  const open = page.locator(".mini-player").getByRole("button").first();
  const dialog = page.getByRole("dialog", { name: "Now playing" });
  await open.click();
  await expect(dialog).toBeVisible();
  await page.getByRole("button", { name: "Close now playing" }).click();
  await expect(dialog).toHaveClass(/is-leaving/);
  expect(
    await dialog.evaluate((el) => getComputedStyle(el).animationName),
  ).toBe("sheet-out");
  await expect(dialog).toBeHidden();

  await page.emulateMedia({ reducedMotion: "reduce" });
  await open.click();
  await expect(dialog).toBeVisible();
  await page.getByRole("button", { name: "Close now playing" }).click();
  await expect(dialog).toBeHidden({ timeout: 100 });
});
