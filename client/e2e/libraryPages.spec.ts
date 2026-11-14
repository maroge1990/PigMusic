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

// A playlist's song menu moves a song up or takes it out, against the
// playlist's revision, then reads the playlist again.
test("a playlist song moves up and leaves the playlist", async ({ page }) => {
  const calls: string[] = [];
  const playlist = {
    id: "p1",
    name: "Road trip",
    revision: 4,
    entries: album.tracks.map((t, position) => ({
      ...t,
      entry_id: "e" + position,
      position,
    })),
  };
  await mockApi(page, {
    answers: {
      "/v1/playlists": (route: Route) =>
        route.fulfill({
          json: {
            playlists: [{ id: "p1", name: "Road trip", track_count: 2 }],
          },
        }),
      "/v1/playlists/p1": (route: Route) => {
        const request = route.request();
        if (request.method() === "PATCH")
          calls.push("PATCH " + request.postData());
        return route.fulfill({ json: playlist });
      },
      "/v1/playlists/p1/songs/1": (route: Route) => {
        const url = new URL(route.request().url());
        calls.push(`${route.request().method()} 1${url.search}`);
        return route.fulfill({ json: {} });
      },
    },
  });
  await page.goto("/#/library/playlist/p1");
  await expect(page.getByRole("heading", { name: "Road trip" })).toBeVisible();
  // The first song can't move up.
  await page
    .getByRole("button", { name: "More options for Von dutch" })
    .click();
  await expect(page.getByRole("menuitem", { name: "Move up" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "More options for 360" }).click();
  await page.getByRole("menuitem", { name: "Move up" }).click();
  await expect.poll(() => calls.length).toBe(1);
  expect(JSON.parse(calls[0].slice("PATCH ".length))).toEqual({
    revision: 4,
    order: [1, 0],
  });
  await page.getByRole("button", { name: "More options for 360" }).click();
  await page.getByRole("menuitem", { name: "Remove from playlist" }).click();
  await expect.poll(() => calls.length).toBe(2);
  expect(calls[1]).toBe("DELETE 1?revision=4");
});

// Removing a song offers Undo, which puts it back and leaves the page.
test("Undo puts a removed song back", async ({ page }) => {
  const calls: string[] = [];
  await mockApi(page, {
    answers: {
      "/v1/library/removal-preview": (route) =>
        route.fulfill({
          json: {
            confirmation: "c",
            delete_count: 0,
            keep_count: 1,
            items: [{ track_id: "t1", delete_file: false, kept_for: ["Sam"] }],
          },
        }),
      "/v1/library/remove": (route) => {
        calls.push("remove " + route.request().postData());
        return route.fulfill({
          json: { id: "removal-1", undo_until: Date.now() / 1000 + 60 },
        });
      },
      "/v1/library/removals/removal-1/undo": (route) => {
        calls.push("undo " + route.request().method());
        return route.fulfill({ json: {} });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page
    .getByRole("button", { name: "More options for Von dutch" })
    .click();
  await page.getByRole("menuitem", { name: "Remove from my library" }).click();
  const dialog = page.getByRole("dialog");
  // Files someone else still wants stay on the server, and the dialog says so.
  await expect(dialog).toContainText(
    "The music files will stay on the server.",
  );
  await expect(dialog).toContainText("Kept for Sam.");
  await dialog.getByRole("button", { name: "Remove" }).click();
  await expect.poll(() => calls.length).toBe(1);
  expect(JSON.parse(calls[0].slice("remove ".length))).toEqual({
    track_ids: ["t1"],
    confirmation: "c",
  });
  const undo = page.getByRole("button", { name: "Undo" });
  await undo.click();
  await expect.poll(() => calls).toContain("undo POST");
  await expect(page.getByRole("status").first()).toContainText("Restored");
  await expect(undo).toHaveCount(0);
  await expect(page).toHaveURL(/#\/library\/[a-z]+$/);
});

// Cancel leaves the song where it was.
test("cancelling a removal keeps the song", async ({ page }) => {
  const calls: string[] = [];
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
      "/v1/library/remove": (route) => {
        calls.push("remove");
        return route.fulfill({ json: { id: "r", undo_until: 0 } });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page
    .getByRole("button", { name: "More options for Von dutch" })
    .click();
  await page.getByRole("menuitem", { name: "Remove from my library" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText(
    "1 music files will move to recoverable trash on the server.",
  );
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page).toHaveURL(/album\/album-1$/);
  expect(calls).toEqual([]);
});

// Playlist options rename a playlist, and delete it only once confirmed.
test("a playlist is renamed, then deleted at once with Undo", async ({
  page,
}) => {
  // D-79 (#183): no dialog; the playlist leaves at once, and the delete is
  // sent after the Undo time unless Undo is pressed.
  await page.clock.install();
  const calls: string[] = [];
  const asked: string[] = [];
  page.on("dialog", (dialog) => {
    asked.push(dialog.message());
    void dialog.dismiss();
  });
  await mockApi(page, {
    answers: {
      "/v1/playlists/p1": (route: Route) => {
        const request = route.request();
        const url = new URL(request.url());
        if (request.method() !== "GET")
          calls.push(
            `${request.method()}${url.search} ${request.postData() ?? ""}`.trim(),
          );
        return route.fulfill({
          json: { id: "p1", name: "Road trip", revision: 2, entries: [] },
        });
      },
    },
  });
  await page.goto("/#/library/playlist/p1");
  await page.getByText("Playlist options").click();
  await page.getByLabel("Playlist name").fill("Long drive");
  await page.getByRole("button", { name: "Rename" }).click();
  await expect.poll(() => calls.length).toBe(1);
  expect(calls[0]).toBe('PATCH {"revision":2,"name":"Long drive"}');
  // Undo within the time: nothing is sent.
  await page.getByRole("button", { name: "Delete playlist" }).click();
  await expect(page).toHaveURL(/#\/library\/[a-z]+$/);
  const undo = page.locator(".undo-bar");
  await expect(undo).toContainText("Road trip deleted.");
  await undo.getByRole("button", { name: "Undo" }).click();
  await expect(undo).toContainText("Road trip is back.");
  await page.clock.runFor(15_000);
  expect(calls).toHaveLength(1);
  // Without Undo, the delete is sent once the time has passed.
  await page.goto("/#/library/playlist/p1");
  await page.getByText("Playlist options").click();
  await page.getByRole("button", { name: "Delete playlist" }).click();
  await page.clock.runFor(5_000);
  expect(calls).toHaveLength(1);
  await page.clock.runFor(6_000);
  await expect.poll(() => calls.length).toBe(2);
  expect(calls[1]).toBe("DELETE?revision=2");
  expect(asked).toEqual([]);
});

test("two playlists deleted in a row each keep their own Undo", async ({
  page,
}) => {
  // Codex, #183 round 1 (medium): a second Undo never hides the first.
  await page.clock.install();
  const deleted: string[] = [];
  const playlist = (id: string, name: string) => (route: Route) => {
    if (route.request().method() === "DELETE") {
      deleted.push(id);
      // The server is slow to answer: the Undo must leave all the same.
      return new Promise<void>(() => {});
    }
    return route.fulfill({ json: { id, name, revision: 1, entries: [] } });
  };
  await mockApi(page, {
    answers: {
      "/v1/playlists/p1": playlist("p1", "Road trip"),
      "/v1/playlists/p2": playlist("p2", "Gym"),
    },
  });
  for (const id of ["p1", "p2"]) {
    await page.goto(`/#/library/playlist/${id}`);
    await page.getByText("Playlist options").click();
    await page.getByRole("button", { name: "Delete playlist" }).click();
    await expect(page).toHaveURL(/#\/library\/[a-z]+$/);
  }
  const bar = page.locator(".undo-bar");
  await expect(bar.getByRole("button", { name: "Undo" })).toHaveCount(2);
  await bar
    .locator("p", { hasText: "Road trip deleted." })
    .getByRole("button", { name: "Undo" })
    .click();
  await expect(bar).toContainText("Road trip is back.");
  await page.clock.runFor(11_000);
  await expect.poll(() => deleted).toEqual(["p2"]);
  // Once sending starts, its Undo is gone, before any answer arrives.
  await expect(bar.getByRole("button", { name: "Undo" })).toHaveCount(0);
});
