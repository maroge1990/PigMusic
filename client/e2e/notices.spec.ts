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
      "/v1/playlists/p1": (route) =>
        route.fulfill({
          json: { id: "p1", name: "Gym", revision: 1, entries: [] },
        }),
      "/v1/playlists/p1/songs": (route) => {
        added = true;
        return route.fulfill({
          json: { id: "p1", name: "Gym", revision: 2, entries: [] },
        });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page
    .getByRole("button", { name: "More options for Von dutch" })
    .click();
  await page.getByRole("menuitem", { name: "Add to playlist" }).click();
  await page.getByRole("menuitem", { name: "Gym" }).click();
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

// Codex (#249): warnings that must stay never hide a fresh notice; older
// ones fold into "N more", which opens them all in place.
test("eight kept warnings fold into 5 more and a fresh error still shows", async ({
  page,
}) => {
  let saved = false;
  await mockApi(page, {
    answers: {
      "/v1/favourites": (route) =>
        "favourite" in route.request().postDataJSON()
          ? route.fulfill({
              status: 503,
              json: { detail: "Favourites are unavailable", code: "x" },
            })
          : route.fulfill({ json: { favourited: false } }),
      "/v1/playlists": (route) =>
        route.fulfill({ json: { playlists: [{ id: "p1", name: "Gym" }] } }),
      "/v1/playlists/p1": (route) =>
        route.fulfill({
          json: { id: "p1", name: "Gym", revision: 1, entries: [] },
        }),
      // The gateway fails after each append: whether it saved is unknown.
      "/v1/playlists/p1/songs": (route) => {
        saved = true;
        return route.fulfill({ status: 502, body: "Bad gateway" });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  const dots = page.getByRole("button", { name: "More options for Von dutch" });
  for (let i = 0; i < 8; i++) {
    await dots.click();
    await page.getByRole("menuitem", { name: "Add to playlist" }).click();
    await page.getByRole("menuitem", { name: "Gym" }).click();
    await expect(notices(page).locator(".notice")).toHaveCount(
      Math.min(i + 1, 3),
    );
  }
  expect(saved).toBe(true);
  // A fresh, unrelated error still shows.
  await dots.click();
  await page.getByRole("menuitem", { name: "Favourite" }).click();
  await expect(notices(page)).toContainText("Favourites are unavailable");
  const more = notices(page).getByRole("button", { name: "5 more" });
  await expect(more).toHaveAttribute("aria-expanded", "false");
  await expect(notices(page).locator(".notice")).toHaveCount(4);
  await more.click();
  await expect(
    notices(page).getByRole("button", { name: "Show fewer" }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(
    notices(page).locator(".notice", { hasText: "Check the playlist" }),
  ).toHaveCount(8);
});
