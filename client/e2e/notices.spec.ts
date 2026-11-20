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
test("eight kept warnings fold into 6 more and a fresh error still shows", async ({
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
      Math.min(i + 1, 2),
    );
  }
  expect(saved).toBe(true);
  // A fresh, unrelated error still shows.
  await dots.click();
  await page.getByRole("menuitem", { name: "Favourite" }).click();
  await expect(notices(page)).toContainText("Favourites are unavailable");
  const more = notices(page).getByRole("button", { name: "6 more" });
  await expect(more).toHaveAttribute("aria-expanded", "false");
  await expect(notices(page).locator(".notice")).toHaveCount(3);
  await more.click();
  await expect(
    notices(page).getByRole("button", { name: "Show fewer" }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(
    notices(page).locator(".notice", { hasText: "Check the playlist" }),
  ).toHaveCount(8);
});

// Codex round 6: an opened list stays inside a small screen; it scrolls,
// and Show fewer stays in view.
test("twelve warnings open in a list that scrolls on a small phone", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 568 });
  await mockApi(page, {
    answers: {
      "/v1/playlists": (route) =>
        route.fulfill({ json: { playlists: [{ id: "p1", name: "Gym" }] } }),
      "/v1/playlists/p1": (route) =>
        route.fulfill({
          json: { id: "p1", name: "Gym", revision: 1, entries: [] },
        }),
      "/v1/playlists/p1/songs": (route) =>
        route.fulfill({ status: 502, body: "Bad gateway" }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  const dots = page.getByRole("button", { name: "More options for Von dutch" });
  for (let i = 0; i < 12; i++) {
    await dots.click();
    await page.getByRole("menuitem", { name: "Add to playlist" }).click();
    await page.getByRole("menuitem", { name: "Gym" }).click();
    await expect(
      page.getByRole("menu", { name: "Add Von dutch to playlist" }),
    ).toBeHidden();
  }
  await notices(page).getByRole("button", { name: "10 more" }).click();
  const fewer = notices(page).getByRole("button", { name: "Show fewer" });
  const list = notices(page)
    .locator(".notice")
    .filter({ hasText: "Check the playlist" });
  await expect(list).toHaveCount(12);
  // The area stays within the screen, with Show fewer in view.
  const box = (await notices(page).boundingBox())!;
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(568);
  await expect(fewer).toBeInViewport();
  // The oldest warning is reached by scrolling the list itself.
  await list.first().scrollIntoViewIfNeeded();
  await expect(list.first()).toBeInViewport();
  await expect(fewer).toBeInViewport();
  await fewer.click();
  await expect(list).toHaveCount(2);
});

// Codex round 7: on a short screen the warnings scroll, but a live Undo
// stays in view below them and works.
test("on a short screen a live Undo stays in view below the warnings", async ({
  page,
}) => {
  await page.setViewportSize({ width: 667, height: 320 });
  await page.clock.install();
  const deleted: string[] = [];
  await mockApi(page, {
    answers: {
      "/v1/playlists": (route) =>
        route.fulfill({
          json: {
            playlists: [
              { id: "p1", name: "Gym" },
              { id: "p3", name: "Old" },
            ],
          },
        }),
      "/v1/playlists/p1": (route) =>
        route.fulfill({
          json: { id: "p1", name: "Gym", revision: 1, entries: [] },
        }),
      "/v1/playlists/p1/songs": (route) =>
        route.fulfill({ status: 502, body: "Bad gateway" }),
      "/v1/playlists/p3": (route) => {
        if (route.request().method() === "DELETE") deleted.push("p3");
        return route.fulfill({
          json: { id: "p3", name: "Old", revision: 1, entries: [] },
        });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  const dots = page.getByRole("button", {
    name: "More options for Von dutch",
  });
  for (let i = 0; i < 2; i++) {
    await dots.click();
    await page.getByRole("menuitem", { name: "Add to playlist" }).click();
    await page.getByRole("menuitem", { name: "Gym" }).click();
    await expect(
      page.getByRole("menu", { name: "Add Von dutch to playlist" }),
    ).toBeHidden();
  }
  await page.goto("/#/library/playlist/p3");
  await page.getByText("Playlist options").click();
  await page.getByRole("button", { name: "Delete playlist" }).click();
  const undo = notices(page).getByRole("button", { name: "Undo" });
  await expect(undo).toBeInViewport({ ratio: 1 });
  await expect(
    notices(page).locator(".notice", { hasText: "Check the playlist" }),
  ).toHaveCount(2);
  await undo.click();
  await expect(notices(page)).toContainText("Old is back.");
  await page.clock.runFor(20_000);
  expect(deleted).toEqual([]);
});

// Codex, batch D web part 2 (#269): on a short landscape screen with three
// warnings and two live Undos, the Undo part left the opened warning list
// no height, so nothing in it could be reached until the Undos were sent.
test("on a short screen with two Undos, opened warnings can still be reached", async ({
  page,
}) => {
  await page.setViewportSize({ width: 667, height: 320 });
  await page.clock.install();
  const playlist = (id: string, name: string) => (route: Route) =>
    route.fulfill({ json: { id, name, revision: 1, entries: [] } });
  await mockApi(page, {
    answers: {
      "/v1/playlists": (route) =>
        route.fulfill({
          json: {
            playlists: [
              { id: "p1", name: "Gym" },
              { id: "p3", name: "Old" },
              { id: "p4", name: "Older" },
            ],
          },
        }),
      "/v1/playlists/p1": playlist("p1", "Gym"),
      "/v1/playlists/p1/songs": (route) =>
        route.fulfill({ status: 502, body: "Bad gateway" }),
      "/v1/playlists/p3": playlist("p3", "Old"),
      "/v1/playlists/p4": playlist("p4", "Older"),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  const dots = page.getByRole("button", {
    name: "More options for Von dutch",
  });
  for (let i = 0; i < 3; i++) {
    await dots.click();
    await page.getByRole("menuitem", { name: "Add to playlist" }).click();
    await page.getByRole("menuitem", { name: "Gym" }).click();
    await expect(
      page.getByRole("menu", { name: "Add Von dutch to playlist" }),
    ).toBeHidden();
  }
  for (const id of ["p3", "p4"]) {
    await page.goto(`/#/library/playlist/${id}`);
    await page.getByText("Playlist options").click();
    await page.getByRole("button", { name: "Delete playlist" }).click();
  }
  const undos = notices(page).getByRole("button", { name: "Undo" });
  await expect(undos).toHaveCount(2);
  await notices(page).getByRole("button", { name: "1 more" }).click();
  const warnings = notices(page)
    .locator(".notice")
    .filter({ hasText: "Check the playlist" });
  await expect(warnings).toHaveCount(3);
  // Each opened warning is reached by scrolling, and each Undo too.
  for (const notice of [...(await warnings.all()), ...(await undos.all())]) {
    await notice.scrollIntoViewIfNeeded();
    await expect(notice).toBeInViewport({ ratio: 0.9 });
  }
  const fewer = notices(page).getByRole("button", { name: "Show fewer" });
  await expect(fewer).toBeInViewport();
  await fewer.click();
  // Folded again, both Undos are reachable.
  for (const undo of await undos.all()) {
    await undo.scrollIntoViewIfNeeded();
    await expect(undo).toBeInViewport({ ratio: 1 });
  }
});
