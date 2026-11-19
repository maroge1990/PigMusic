import { expect, test, type Page, type Route } from "@playwright/test";
import { album, mockApi } from "./mock";

// Codex round 2 (#251): Library Play and a Songs row read the library as
// one source of songs, a page at a time.
type Song = { id: string; title: string; available?: boolean };
const song = (i: number | string): Song => ({
  ...album.tracks[0],
  id: "lib-" + i,
  title: "Song " + i,
});
/** A page of `size` songs from `list`, as the server answers it. */
const pageOf = (
  list: Song[],
  offset: number,
  size: number,
  revision = "r1",
) => ({
  items: list.slice(offset, offset + size),
  total: list.length,
  offset,
  next_offset: offset + size < list.length ? offset + size : null,
  revision,
});
const offsetOf = (route: Route) =>
  Number(new URL(route.request().url()).searchParams.get("offset"));
const range = (n: number) => Array.from({ length: n }, (_, i) => song(i));

async function setUp(
  page: Page,
  tracks: (route: Route) => Promise<void> | void,
) {
  await mockApi(page, {
    answers: { "/v1/library-pages/tracks": async (route) => tracks(route) },
  });
  await page.route("**/api/v1/playback/*/url", (route) =>
    route.fulfill({
      json: { url: "/api/v1/playback/files/silence", expires_in: 60 },
    }),
  );
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
}
const libraryPlay = (page: Page) =>
  page.locator(".library-toolbar").getByRole("button", { name: "Play" });
const savedTitles = (page: Page) =>
  page.evaluate(() =>
    (
      JSON.parse(localStorage.getItem("pig-music-queue") || "null")?.songs || []
    ).map((s: { title: string }) => s.title),
  );

test("a failed page leaves Next on, says so, and Try again reads it", async ({
  page,
}) => {
  const list = range(6);
  let failures = 1;
  await setUp(page, (route) => {
    const offset = offsetOf(route);
    if (offset === 3 && failures-- > 0)
      return route.fulfill({
        status: 503,
        json: { detail: "The library is busy.", code: "busy" },
      });
    return route.fulfill({ json: pageOf(list, offset, 3) });
  });
  await page.goto("/#/library/albums");
  await libraryPlay(page).click();
  const mini = page.locator(".mini-player");
  await expect(mini).toContainText("Song 0");
  const notices = page.locator(".notice-area");
  await expect(notices).toContainText("busy");
  const next = mini.getByRole("button", { name: "Next song" });
  await next.click();
  await next.click();
  await expect(mini).toContainText("Song 2");
  // The last loaded song: more is still to come, so Next stays on.
  await expect(next).toBeEnabled();
  await notices.getByRole("button", { name: "Try again" }).click();
  await next.click();
  await expect(mini).toContainText("Song 3");
});

test("Library Play reads on past a page with nothing playable", async ({
  page,
}) => {
  const list = range(6).map((s, i) => (i < 3 ? { ...s, available: false } : s));
  await setUp(page, (route) =>
    route.fulfill({ json: pageOf(list, offsetOf(route), 3) }),
  );
  await page.goto("/#/library/albums");
  await libraryPlay(page).click();
  await expect(page.locator(".mini-player")).toContainText("Song 3");
});

test("a library edit between pages repeats no song", async ({ page }) => {
  const before = range(9);
  // A song added at the top moves every other song down by one.
  const after = [song("new"), ...before];
  let reads = 0;
  await setUp(page, (route) => {
    const offset = offsetOf(route);
    reads++;
    return route.fulfill({
      json:
        reads === 1
          ? pageOf(before, offset, 3, "r1")
          : pageOf(after, offset, 3, "r2"),
    });
  });
  await page.goto("/#/library/albums");
  await libraryPlay(page).click();
  const mini = page.locator(".mini-player");
  await expect(mini).toContainText("Song 0");
  const next = mini.getByRole("button", { name: "Next song" });
  for (let i = 0; i < 6; i++) await next.click();
  await expect
    .poll(async () => (await savedTitles(page)).length)
    .toBeGreaterThanOrEqual(7);
  const titles: string[] = await savedTitles(page);
  expect(new Set(titles).size).toBe(titles.length);
  expect(titles.slice(0, 3)).toEqual(["Song 0", "Song 1", "Song 2"]);
});

test("a Songs row plays on past the loaded rows", async ({ page }) => {
  const list = range(250);
  await setUp(page, (route) =>
    route.fulfill({ json: pageOf(list, offsetOf(route), 200) }),
  );
  await page.goto("/#/library/songs");
  const rows = page.locator(".song-list .song-row");
  await expect(rows).toHaveCount(200);
  await rows.filter({ hasText: "Song 199" }).click();
  const mini = page.locator(".mini-player");
  await expect(mini).toContainText("Song 199");
  await mini.getByRole("button", { name: "Next song" }).click();
  await expect(mini).toContainText("Song 200");
});

test("Repeat All reads the next page before wrapping to the first song", async ({
  page,
}) => {
  const list = range(6);
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  await setUp(page, async (route) => {
    const offset = offsetOf(route);
    if (offset === 3) await held;
    return route.fulfill({ json: pageOf(list, offset, 3) });
  });
  await page.addInitScript(() =>
    localStorage.setItem("pig-music-repeat", "all"),
  );
  await page.goto("/#/library/albums");
  await libraryPlay(page).click();
  const mini = page.locator(".mini-player");
  await expect(mini).toContainText("Song 0");
  const next = mini.getByRole("button", { name: "Next song" });
  await next.click();
  await next.click();
  await expect(mini).toContainText("Song 2");
  await next.click();
  release();
  await expect(mini).toContainText("Song 3");
});
