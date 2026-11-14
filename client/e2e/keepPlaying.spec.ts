import { expect, test, type Page, type Route } from "@playwright/test";
import { album, mockApi } from "./mock";

const similar = (id: string, title: string) => ({
  ...album.tracks[0],
  id,
  title,
  album: "Elsewhere",
  album_id: "album-9",
});

/** The mock album with audio that only ends when the test says so. */
async function albumPlayer(
  page: Page,
  answers: Record<string, (route: Route) => Promise<void>> = {},
) {
  const mock = await mockApi(page, { answers });
  await page.route("**/api/v1/playback/*/url", (route) =>
    route.fulfill({
      json: { url: "/api/v1/playback/files/silence", expires_in: 60 },
    }),
  );
  await page.route("**/api/v1/playback/files/silence", (route) =>
    route.fulfill({ contentType: "audio/wav", body: Buffer.alloc(64) }),
  );
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
  return mock;
}

/** Plays the album from its first song and moves to its last. */
async function playToLastSong(page: Page) {
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  const mini = page.locator(".mini-player");
  await expect(mini).toContainText("Von dutch");
  await mini.getByRole("button", { name: "Next song" }).click();
  await expect(mini).toContainText("360");
  await expect(mini.getByRole("button", { name: "Next song" })).toBeDisabled();
  return mini;
}

const finishSong = (page: Page) => page.locator("audio").dispatchEvent("ended");

// #202: an album that ends carries on with similar songs.
test("an ended album continues with similar songs, listed under Similar songs", async ({
  page,
}) => {
  const asked: unknown[] = [];
  const skips: unknown[] = [];
  await albumPlayer(page, {
    "/v1/radio/continuation": (route) => {
      asked.push(route.request().postDataJSON());
      return route.fulfill({
        json: {
          id: "similar",
          title: "Similar songs",
          preparing: false,
          tracks:
            asked.length === 1
              ? [
                  similar("lib-9", "Similar one"),
                  {
                    ...similar("mix:edition-1:4", "Similar two"),
                    server_state: "not_in_library",
                  },
                ]
              : [],
        },
      });
    },
    "/v1/radio/feedback": (route) => {
      skips.push(route.request().postDataJSON());
      return route.fulfill({ json: { recorded: true } });
    },
  });
  const mini = await playToLastSong(page);
  await finishSong(page);
  await expect(mini).toContainText("Similar one");
  expect(asked).toEqual([{ seeds: ["t1", "t2"], exclude: ["t1", "t2"] }]);
  await mini.getByRole("button").first().click();
  const upNext = page.getByRole("region", { name: "Up next" });
  await expect(upNext.getByRole("heading", { level: 4 })).toHaveText(
    "Similar songs",
  );
  await expect(upNext.locator("li strong")).toHaveText(["Similar two"]);
  // A similar song skipped early steers what comes next, by its library id.
  const dialog = page.getByRole("dialog", { name: "Now playing" });
  await dialog.getByRole("button", { name: "Next song" }).click();
  await expect(dialog.getByRole("heading", { level: 2 })).toHaveText(
    "Similar two",
  );
  await expect
    .poll(() => skips)
    .toEqual([{ track_id: "lib-9", action: "skip" }]);
  // The similar songs are saved with the queue, still marked as similar.
  const saved = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("pig-music-queue")!),
  );
  expect(saved.songs.map((s: { similar?: boolean }) => !!s.similar)).toEqual([
    false,
    false,
    true,
    true,
  ]);
});

test("with keep playing off, an ended album stops", async ({ page }) => {
  let asked = 0;
  let preferences = 0;
  await albumPlayer(page, {
    "/v1/preferences": (route) => {
      preferences++;
      return route.fulfill({
        json: {
          appearance: "system",
          default_tab: "artists",
          keep_playing: false,
        },
      });
    },
    "/v1/radio/continuation": (route) => {
      asked++;
      return route.fulfill({
        json: { id: "s", title: "Similar songs", preparing: false, tracks: [] },
      });
    },
  });
  const mini = await playToLastSong(page);
  expect(preferences).toBeGreaterThan(0);
  await finishSong(page);
  await page.waitForTimeout(500);
  expect(asked).toBe(0);
  await expect(mini).toContainText("360");
  await expect(mini.getByRole("button", { name: "Next song" })).toBeDisabled();
});

test("the Playback setting saves keep playing", async ({ page }) => {
  const saved: unknown[] = [];
  let stored = {
    appearance: "dark",
    default_tab: "artists",
    keep_playing: true,
  };
  await mockApi(page, {
    answers: {
      "/v1/preferences": async (route) => {
        if (route.request().method() === "PUT") {
          const body = route.request().postDataJSON();
          saved.push(body);
          stored = body;
        }
        return route.fulfill({ json: stored });
      },
      "/v1/quality": (route) =>
        route.fulfill({
          json: { preferences: [], options: [], household_default: "" },
        }),
      "/v1/me/sessions": (route) => route.fulfill({ json: { sessions: [] } }),
    },
  });
  await page.goto("/#/account");
  const box = page.getByRole("checkbox", {
    name: "Keep playing similar songs when the music ends",
  });
  await expect(box).toBeChecked();
  await box.uncheck();
  await expect
    .poll(() => saved)
    .toEqual([
      { appearance: "dark", default_tab: "artists", keep_playing: false },
    ]);
  await expect(box).not.toBeChecked();
  expect(
    await page.evaluate(() => localStorage.getItem("pig-music-keep-playing")),
  ).toBe("false");
});
