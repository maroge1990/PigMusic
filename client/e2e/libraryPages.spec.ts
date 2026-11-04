import { expect, test, type Route } from "@playwright/test";
import { album, mockApi } from "./mock";

const second = {
  ...album,
  id: "album-2",
  title: "Sucker",
  tracks: album.tracks.map((t) => ({
    ...t,
    id: t.id + "-b",
    title: t.title + " (Sucker)",
    album: "Sucker",
    album_id: "album-2",
  })),
};

// #97: a detail page never shows the previous one while the next loads, so
// Play can't start the old list.
test("an album page waits for its own songs before offering Play", async ({
  page,
}) => {
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  await mockApi(page, {
    answers: {
      "/v1/albums/album-2": async (route) => {
        await held;
        await route.fulfill({ json: second });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await expect(page.getByRole("heading", { name: "Brat" })).toBeVisible();
  await page.goto("/#/library/album/album-2");
  await expect(
    page.getByRole("status").filter({ hasText: "Loading" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Brat" })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Play", exact: true }),
  ).toHaveCount(0);
  release();
  await expect(page.getByRole("heading", { name: "Sucker" })).toBeVisible();
  await expect(page.getByText("Von dutch (Sucker)")).toBeVisible();
});

// #97: an error from one page doesn't follow you to the next.
test("an error is cleared by opening another page", async ({ page }) => {
  await mockApi(page, {
    answers: {
      "/v1/albums/album-2": (route) =>
        route.fulfill({
          status: 500,
          json: { detail: "The album could not be read", code: "internal" },
        }),
    },
  });
  await page.goto("/#/library/album/album-2");
  await expect(page.getByRole("alert")).toContainText(
    "The album could not be read",
  );
  await page.goto(`/#/library/album/${album.id}`);
  await expect(page.getByRole("heading", { name: "Brat" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

// #97: an older refresh answering last never replaces a newer one. The first
// refresh still waits for the favourites when the second finds them emptied.
test("a slow earlier refresh doesn't overwrite a newer one", async ({
  page,
}) => {
  let lists = 0;
  let releaseFirst: () => void = () => {};
  const first = new Promise<void>((resolve) => (releaseFirst = resolve));
  const favourites = "pig-favourites:u1";
  await mockApi(page, {
    answers: {
      "/v1/playlists": (route: Route) =>
        route.fulfill({
          json: {
            playlists:
              ++lists === 1
                ? [{ id: favourites, name: "Favourites", track_count: 1 }]
                : [],
          },
        }),
      ["/v1/playlists/" + favourites]: async (route: Route) => {
        await first;
        await route.fulfill({
          json: {
            id: favourites,
            name: "Favourites",
            revision: 1,
            favourites: true,
            entries: [
              {
                ...album.tracks[0],
                title: "Old favourite",
                entry_id: "e1",
                position: 0,
              },
            ],
          },
        });
      },
    },
  });
  await page.goto("/#/library/favourites");
  await expect.poll(() => lists).toBe(1);
  await page.evaluate(() =>
    window.dispatchEvent(new Event("music-library-changed")),
  );
  await expect.poll(() => lists).toBe(2);
  await expect(page.getByText("No favourites yet")).toBeVisible();
  releaseFirst();
  await page.waitForTimeout(500);
  await expect(page.getByText("Old favourite")).toHaveCount(0);
  await expect(page.getByText("No favourites yet")).toBeVisible();
});

// #97: after removing a song the page leaves the album's address.
test("removing a song returns to the library's address", async ({ page }) => {
  await mockApi(page, {
    answers: {
      "/v1/library/removal-preview": (route) =>
        route.fulfill({
          json: {
            confirmation: "c",
            delete_count: 1,
            keep_count: 0,
            items: [{ track_id: "t1", delete_file: true, kept_for: [] }],
          },
        }),
      "/v1/library/remove": (route) =>
        route.fulfill({
          json: { id: "removal-1", undo_until: Date.now() / 1000 + 60 },
        }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page
    .getByRole("button", { name: "More options for Von dutch" })
    .click();
  await page.getByRole("menuitem", { name: "Remove from my library" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Remove" })
    .click();
  await expect(page.getByRole("status").first()).toContainText(
    "Removed from your library",
  );
  await expect(page).not.toHaveURL(/album/);
  await expect(page).toHaveURL(/#\/library\/[a-z]+$/);
});
