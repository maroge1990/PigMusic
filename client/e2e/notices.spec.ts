import { expect, test, type Route } from "@playwright/test";
import { album, mockApi } from "./mock";

// #250: every action says how it went in the one notice area.

const notices = (page: import("@playwright/test").Page) =>
  page.locator(".notice-area");

test("favouriting a song says so, and the notice can be closed", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/favourites": (route) =>
        route.fulfill({ json: { favourited: false } }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page
    .getByRole("button", { name: "More options for Von dutch" })
    .click();
  await page.getByRole("menuitem", { name: "Favourite" }).click();
  const notice = notices(page).getByRole("status");
  await expect(notice).toHaveText("Added to favourites");
  await notice.getByRole("button", { name: "Close" }).click();
  await expect(notices(page)).toBeEmpty();
});

test("adding a song to a playlist says so where you can see it", async ({
  page,
}) => {
  let added = false;
  await mockApi(page, {
    answers: {
      "/v1/favourites": (route) =>
        route.fulfill({ json: { favourited: false } }),
      "/v1/playlists": (route) =>
        route.fulfill({ json: { playlists: [{ id: "p1", name: "Gym" }] } }),
      "/v1/playlists/p1/songs": (route) => {
        added = true;
        return route.fulfill({ json: {} });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page
    .getByRole("button", { name: "More options for Von dutch" })
    .click();
  await page.getByRole("menuitem", { name: "Add to playlist…" }).click();
  await page.getByRole("button", { name: "Gym" }).click();
  await expect(notices(page).getByRole("status")).toHaveText(
    "Added to playlist",
  );
  await expect(notices(page).getByRole("status")).toBeVisible();
  expect(added).toBe(true);
});

test("removing a song from a playlist says Removed, not Saved", async ({
  page,
}) => {
  const calls: string[] = [];
  const entry = { ...album.tracks[0], entry_id: "e0", position: 0 };
  await mockApi(page, {
    answers: {
      "/v1/playlists/p1": (route) =>
        route.fulfill({
          json: { id: "p1", name: "Gym", revision: 1, entries: [entry] },
        }),
      "/v1/playlists/p1/songs/0": (route: Route) => {
        calls.push(route.request().method());
        return route.fulfill({ json: {} });
      },
    },
  });
  await page.goto("/#/library/playlist/p1");
  await page
    .getByRole("button", { name: "More options for Von dutch" })
    .click();
  await page.getByRole("menuitem", { name: "Remove from playlist" }).click();
  await expect(notices(page)).toContainText("Removed from playlist");
  await expect(page.getByText("Saved", { exact: true })).toHaveCount(0);
  expect(calls).toEqual(["DELETE"]);
});
