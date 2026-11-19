import { expect, test, type Page, type Route } from "@playwright/test";
import { album, mockApi } from "./mock";

const song = (i: number) => ({
  ...album.tracks[0],
  id: "lib-" + i,
  title: "Song " + i,
});
/** The library's songs as the server pages them. */
const songPage = (offset: number, total: number, revision = "r1") => {
  const end = Math.min(total, offset + 200);
  return {
    items: Array.from({ length: end - offset }, (_, i) => song(offset + i)),
    total,
    offset,
    next_offset: end < total ? end : null,
    revision,
  };
};
const offsetOf = (route: Route) =>
  Number(new URL(route.request().url()).searchParams.get("offset"));
const rows = (page: Page) => page.locator(".song-list .song-row");
const scrollToEnd = (page: Page) =>
  page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));

// Codex round 1, findings 2 and 3 (#251): the Songs tab shows its first
// page at once and reads the next as you scroll near the end.
test("the Songs tab shows the first page at once and the next on scrolling", async ({
  page,
}) => {
  const offsets: number[] = [];
  await mockApi(page, {
    answers: {
      "/v1/library-pages/tracks": (route) => {
        offsets.push(offsetOf(route));
        return route.fulfill({ json: songPage(offsetOf(route), 250) });
      },
    },
  });
  await page.goto("/#/library/songs");
  await expect(rows(page)).toHaveCount(200);
  await expect(page.locator(".songs-tab-header")).toContainText("250 songs");
  await expect(
    page.locator(".songs-tab-header").getByRole("button", { name: "Play" }),
  ).toBeEnabled();
  await expect(
    page.locator(".songs-tab-header").getByRole("button", { name: "Shuffle" }),
  ).toBeEnabled();
  // The server keeps the order: no sort to offer yet.
  await expect(
    page.locator(".songs-tab-header").getByLabel("Sort"),
  ).toHaveCount(0);
  expect(offsets).toEqual([0]);
  await scrollToEnd(page);
  await expect(rows(page)).toHaveCount(250);
  await expect(rows(page).last()).toContainText("Song 249");
  expect(offsets).toEqual([0, 200]);
});

// A page from a later library state than the first starts the list again,
// so the rows never mix two states.
test("the Songs tab starts again when the library changes between pages", async ({
  page,
}) => {
  const offsets: number[] = [];
  await mockApi(page, {
    answers: {
      "/v1/library-pages/tracks": (route) => {
        const offset = offsetOf(route);
        offsets.push(offset);
        // The library changes after the first page: one song more.
        const changed = offsets.length > 1;
        return route.fulfill({
          json: songPage(offset, changed ? 251 : 250, changed ? "r2" : "r1"),
        });
      },
    },
  });
  await page.goto("/#/library/songs");
  await expect(rows(page)).toHaveCount(200);
  await scrollToEnd(page);
  await expect(page.locator(".songs-tab-header")).toContainText("251 songs");
  await expect.poll(() => offsets.slice(0, 3)).toEqual([0, 200, 0]);
  await scrollToEnd(page);
  await expect(rows(page)).toHaveCount(251);
});

// A page that fails says so with Try again for that page; success clears it.
test("a Songs page that fails offers Try again for that page", async ({
  page,
}) => {
  let failures = 1;
  await mockApi(page, {
    answers: {
      "/v1/library-pages/tracks": (route) => {
        const offset = offsetOf(route);
        if (offset === 200 && failures-- > 0)
          return route.fulfill({
            status: 503,
            json: { detail: "The library is busy.", code: "busy" },
          });
        return route.fulfill({ json: songPage(offset, 250) });
      },
    },
  });
  await page.goto("/#/library/songs");
  await expect(rows(page)).toHaveCount(200);
  await scrollToEnd(page);
  const notices = page.locator(".notice-area");
  await expect(notices).toContainText("busy");
  await notices.getByRole("button", { name: "Try again" }).click();
  await expect(rows(page)).toHaveCount(250);
  await expect(notices).not.toContainText("busy");
});

// Codex round 1, finding 7: a song past the 500th in its list resumes as
// itself after a reload, not as the 500th.
test("a Songs row beyond 500 resumes as itself after a reload", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/library-pages/tracks": (route) =>
        route.fulfill({ json: songPage(offsetOf(route), 600) }),
      "/v1/playback/lib-550/url": (route) =>
        route.fulfill({
          json: { url: "/api/v1/playback/files/silence", expires_in: 60 },
        }),
    },
  });
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
  await page.goto("/#/library/songs");
  for (const count of [200, 400, 600]) {
    await expect(rows(page)).toHaveCount(count);
    await scrollToEnd(page);
  }
  await rows(page).filter({ hasText: "Song 550" }).click();
  const mini = page.locator(".mini-player");
  await expect(mini).toContainText("Song 550");
  await expect
    .poll(() =>
      page.evaluate(() => {
        const saved = JSON.parse(
          localStorage.getItem("pig-music-queue") || "null",
        );
        return saved?.songs[saved.cursor]?.title;
      }),
    )
    .toBe("Song 550");
  await page.reload();
  await expect(mini).toContainText("Song 550");
});
