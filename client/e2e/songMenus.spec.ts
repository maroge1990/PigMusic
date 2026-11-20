import { expect, test, type Page } from "@playwright/test";
import { album, artist, mockApi } from "./mock";

// D-118 (#248, #249): one song row, one song menu and one collection menu.

const queued = (page: Page) =>
  page.evaluate(
    () =>
      JSON.parse(localStorage.getItem("pig-music-queue") || "null")?.songs.map(
        (s: { title: string }) => s.title,
      ) ?? [],
  );

/** Every song gets an address, so the queue is saved as it starts. */
async function playable(page: Page) {
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
  await page.route("**/api/v1/playback/*/url", (route) =>
    route.fulfill({
      json: { url: "/api/v1/playback/files/silence", expires_in: 60 },
    }),
  );
}

const songMenuWords = [
  "Play next",
  "Add to queue",
  "Favourite",
  "Add to playlist",
  "Download",
  "Remove from library",
];

test("right-click and a long press open the same menu as ⋯", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/favourites": (route) =>
        route.fulfill({ json: { favourited: false } }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  const row = page.locator(".song-row", { hasText: "Von dutch" });
  const menu = page.getByRole("menu", { name: "Von dutch" });
  await row.getByRole("button", { name: "More options for Von dutch" }).click();
  await expect(menu.getByRole("menuitem")).toHaveText(songMenuWords);
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await row.locator(".song-text").click({ button: "right" });
  await expect(menu).toBeVisible();
  await expect(menu.getByRole("menuitem")).toHaveText(songMenuWords);
  await page.keyboard.press("Escape");
  await row
    .locator(".song-text")
    .dispatchEvent("pointerdown", { pointerType: "touch" });
  await expect(menu).toBeVisible();
  await expect(menu.getByRole("menuitem")).toHaveText(songMenuWords);
  // Every item has its icon (DESIGN.md's table).
  for (const item of await menu.getByRole("menuitem").all())
    await expect(item.locator("svg")).toHaveCount(1);
});

test("the song menu moves with the arrow keys and Escape returns focus", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/favourites": (route) =>
        route.fulfill({ json: { favourited: false } }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  const dots = page.getByRole("button", { name: "More options for 360" });
  await dots.click();
  const menu = page.getByRole("menu", { name: "360" });
  const item = (name: string) =>
    menu.getByRole("menuitem", { name, exact: true });
  await expect(item("Play next")).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(item("Add to queue")).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("ArrowUp");
  await expect(item("Remove from library")).toBeFocused();
  await page.keyboard.press("Home");
  await expect(item("Play next")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(dots).toBeFocused();
});

test("Add to playlist from the menu is the one picker, with arrow keys", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/favourites": (route) =>
        route.fulfill({ json: { favourited: false } }),
      "/v1/playlists": (route) =>
        route.fulfill({
          json: {
            playlists: [
              { id: "p1", name: "Gym" },
              { id: "p2", name: "Road trip" },
            ],
          },
        }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  const dots = page.getByRole("button", { name: "More options for 360" });
  await dots.click();
  await page.getByRole("menuitem", { name: "Add to playlist" }).click();
  const picker = page.getByRole("menu", { name: "Add 360 to playlist" });
  await expect(picker.getByRole("menuitem", { name: "Gym" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(
    picker.getByRole("menuitem", { name: "Road trip" }),
  ).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(
    picker.getByRole("menuitem", { name: "New playlist" }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(picker).toBeHidden();
  await expect(dots).toBeFocused();
});

// #275: a new playlist the server couldn't confirm may have been made; the
// picker's dialog must not offer Create again for a second copy.
test("an unconfirmed new playlist from the picker is not offered again", async ({
  page,
}) => {
  let creates = 0;
  await mockApi(page, {
    answers: {
      "/v1/favourites": (route) =>
        route.fulfill({ json: { favourited: false } }),
      "/v1/playlists": (route) => {
        if (route.request().method() !== "POST")
          return route.fulfill({ json: { playlists: [] } });
        creates++;
        return route.fulfill({
          status: 504,
          contentType: "text/html",
          body: "<html><body>504 Gateway Time-out</body></html>",
        });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByRole("button", { name: "More options for 360" }).click();
  await page.getByRole("menuitem", { name: "Add to playlist" }).click();
  await page.getByRole("menuitem", { name: "New playlist" }).click();
  const dialog = page.locator(".new-playlist-dialog");
  await dialog.getByLabel("Playlist name").fill("Road trip");
  await dialog.getByRole("button", { name: "Create playlist" }).click();
  await expect(page.locator(".notice-area")).toContainText(
    "may or may not have been saved",
  );
  await expect(dialog).toBeHidden();
  expect(creates).toBe(1);
});

// Codex, batch F (#275): a create whose answer was lost (the connection
// dropped after the server saved it) left Create ready for a second copy.
test("a new playlist from the picker whose answer is lost is not offered again", async ({
  page,
}) => {
  let creates = 0;
  await mockApi(page, {
    answers: {
      "/v1/favourites": (route) =>
        route.fulfill({ json: { favourited: false } }),
      "/v1/playlists": (route) => {
        if (route.request().method() !== "POST")
          return route.fulfill({ json: { playlists: [] } });
        creates++;
        return route.abort("connectionreset");
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByRole("button", { name: "More options for 360" }).click();
  await page.getByRole("menuitem", { name: "Add to playlist" }).click();
  await page.getByRole("menuitem", { name: "New playlist" }).click();
  const dialog = page.locator(".new-playlist-dialog");
  await dialog.getByLabel("Playlist name").fill("Road trip");
  await dialog.getByRole("button", { name: "Create playlist" }).click();
  await expect(page.locator(".notice-area")).toContainText(
    "may or may not have been saved",
  );
  await expect(dialog).toBeHidden();
  expect(creates).toBe(1);
});

// Codex, batch F (#275): the playlist was made but adding the song failed;
// Create stayed the retry, and a second click made a second playlist.
test("a made playlist whose add fails retries the add, never the create", async ({
  page,
}) => {
  let creates = 0;
  const adds: string[] = [];
  await mockApi(page, {
    answers: {
      "/v1/favourites": (route) =>
        route.fulfill({ json: { favourited: false } }),
      "/v1/playlists": (route) => {
        if (route.request().method() !== "POST")
          return route.fulfill({ json: { playlists: [] } });
        creates++;
        return route.fulfill({
          status: 201,
          json: { id: "p1", name: "Road trip" },
        });
      },
      "/v1/playlists/p1": (route) =>
        route.fulfill({
          json: { id: "p1", name: "Road trip", revision: 1, entries: [] },
        }),
      "/v1/playlists/p1/songs": (route) => {
        adds.push(route.request().url());
        // Refused once (the playlist moved), then added.
        return adds.length === 1
          ? route.fulfill({
              status: 409,
              json: { code: "revision_conflict", detail: "Try again." },
            })
          : route.fulfill({ json: { revision: 2 } });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByRole("button", { name: "More options for 360" }).click();
  await page.getByRole("menuitem", { name: "Add to playlist" }).click();
  await page.getByRole("menuitem", { name: "New playlist" }).click();
  const dialog = page.locator(".new-playlist-dialog");
  await dialog.getByLabel("Playlist name").fill("Road trip");
  await dialog.getByRole("button", { name: "Create playlist" }).click();
  await expect.poll(() => adds.length).toBe(1);
  await expect(dialog).toBeHidden();
  await page
    .locator(".notice-area")
    .getByRole("button", { name: "Try again" })
    .click();
  await expect.poll(() => adds.length).toBe(2);
  expect(creates).toBe(1);
});

test("a song tapped in search plays alone; on an album it plays on", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/search/results": (route) =>
        route.fulfill({
          json: {
            top: [],
            songs: album.tracks,
            albums: [],
            artists: [],
            playlists: [],
          },
        }),
      "/v1/catalogue/artists": (route) =>
        route.fulfill({
          json: { artists: [], offset: 0, total: 0, next_offset: null },
        }),
    },
  });
  await playable(page);
  await page.goto("/#/search?q=Charli");
  await page.locator(".song-row", { hasText: "Von dutch" }).click();
  await expect.poll(() => queued(page)).toEqual(["Von dutch"]);
  await page.goto(`/#/library/album/${album.id}`);
  await page.locator(".song-row", { hasText: "Von dutch" }).click();
  await expect.poll(() => queued(page)).toEqual(["Von dutch", "360"]);
});

test("a chart row shows the artist and the album", async ({ page }) => {
  const song = {
    id: "s1",
    name: "Song one",
    title: "Song one",
    artist: "Someone",
    artist_id: "a-s1",
    album: "Album one",
    duration: 200,
  };
  await mockApi(page, {
    answers: {
      "/v1/home/popular": (route) =>
        route.fulfill({
          json: {
            state: "ready",
            title: "Popular in Australia",
            artists: [],
            albums: [],
            songs: [song],
          },
        }),
    },
  });
  await page.goto("/#/home");
  await expect(
    page.locator(".home-songs .song-row", { hasText: "Song one" }),
  ).toContainText("Someone · Album one");
});

test("an artist's popular songs still name the artist", async ({ page }) => {
  await mockApi(page, {
    answers: {
      [`/v1/catalogue/artists/${artist.id}/popularity`]: (route) =>
        route.fulfill({
          json: {
            enabled: true,
            songs: [
              {
                id: "rec-1",
                title: "Von dutch",
                artist: artist.name,
                album: "Brat",
                release_id: "rel-1",
                duration: 180,
              },
            ],
            albums: [],
            ranked_album_ids: [],
          },
        }),
    },
  });
  await page.goto(
    `/#/search/artist/${artist.id}?name=${encodeURIComponent(artist.name)}`,
  );
  await expect(
    page.locator(".artist-popular .song-row", { hasText: "Von dutch" }),
  ).toContainText(`${artist.name} · Brat`);
});

test("Up next rows have the cover and the song menu", async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page.locator(".song-row", { hasText: "Von dutch" }).click();
  await page.locator(".mini-player").getByRole("button").first().click();
  const upNext = page.getByRole("region", { name: "Up next" });
  const row = upNext.locator(".song-row", { hasText: "360" });
  await expect(row.locator(".cover")).toHaveCount(1);
  await row.getByRole("button", { name: "More options for 360" }).click();
  const menu = page.getByRole("menu", { name: "360" });
  await expect(menu.getByRole("menuitem")).toHaveText([
    "Play next",
    "Add to queue",
    "Favourite",
    "Add to playlist",
    "Download",
    "Go to album",
    "Go to artist",
    "Remove from Up next",
    "Remove from library",
  ]);
  // Escape closes the menu, not Now Playing.
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(upNext).toBeVisible();
});

test("a favourite shows a small star on its row", async ({ page }) => {
  const favourites = "pig-favourites:u1";
  await mockApi(page, {
    answers: {
      "/v1/playlists": (route) =>
        route.fulfill({
          json: { playlists: [{ id: favourites, name: "Favourites" }] },
        }),
      ["/v1/playlists/" + favourites]: (route) =>
        route.fulfill({
          json: {
            id: favourites,
            name: "Favourites",
            revision: 1,
            favourites: true,
            entries: [{ ...album.tracks[1], entry_id: "e1", position: 0 }],
          },
        }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await expect(
    page
      .locator(".song-row", { hasText: "360" })
      .getByRole("img", { name: "Favourite" }),
  ).toBeVisible();
  await expect(
    page
      .locator(".song-row", { hasText: "Von dutch" })
      .getByRole("img", { name: "Favourite" }),
  ).toHaveCount(0);
});

const collectionWords = [
  "Play",
  "Shuffle",
  "Play next",
  "Add to library",
  "Add to playlist",
  "Download",
];

test("album, artist and playlist cards have the collection menu", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/playlists": (route) =>
        route.fulfill({
          json: {
            playlists: [{ id: "p1", name: "Road trip", track_count: 2 }],
          },
        }),
      "/v1/playlists/p1": (route) =>
        route.fulfill({
          json: {
            id: "p1",
            name: "Road trip",
            revision: 1,
            entries: album.tracks.map((t, position) => ({
              ...t,
              saved_track: t,
              entry_id: "e" + position,
              position,
            })),
          },
        }),
    },
  });
  await playable(page);
  for (const [tab, name] of [
    ["albums", album.title],
    ["artists", artist.name],
    ["playlists", "Road trip"],
  ]) {
    await page.goto("/#/library/" + tab);
    await page
      .getByRole("button", { name: `More options for ${name}` })
      .click();
    const menu = page.getByRole("menu", { name });
    await expect(menu.getByRole("menuitem")).toHaveText(collectionWords);
    for (const item of await menu.getByRole("menuitem").all())
      await expect(item.locator("svg")).toHaveCount(1);
    await page.keyboard.press("Escape");
  }
  // Play from the playlist's menu plays its songs in order.
  await page
    .getByRole("link", { name: /Road trip/ })
    .click({ button: "right" });
  await page
    .getByRole("menu", { name: "Road trip" })
    .getByRole("menuitem", { name: "Play", exact: true })
    .click();
  await expect.poll(() => queued(page)).toEqual(["Von dutch", "360"]);
});

// Codex (#249): Up next is the player's own copy; editing it leaves the mix
// page's list, and what its menu plays, as they were.
test("editing Up next leaves the mix page and its menu unchanged", async ({
  page,
}) => {
  const tracks = [0, 1, 2, 3].map((i) => ({
    ...album.tracks[0],
    id: `mix:edition-0:${i}`,
    title: "Mix song " + i,
    server_state: "not_in_library",
  }));
  await mockApi(page, {
    answers: {
      "/v1/home/mixes": (route) =>
        route.fulfill({
          json: {
            mixes: [
              {
                id: "edition-0",
                kind: "daily",
                title: "Daily blend",
                day: "Tuesday",
                reason: "Music close to your taste",
                tracks,
              },
            ],
            preparing: false,
          },
        }),
    },
  });
  await playable(page);
  await page.goto("/#/home/mix/edition-0");
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await page.locator(".mini-player").getByRole("button").first().click();
  const upNext = page.getByRole("region", { name: "Up next" });
  // Remove one, move one, and put one in to play next.
  await upNext
    .getByRole("button", { name: "More options for Mix song 1" })
    .click();
  await page
    .getByRole("menu", { name: "Mix song 1", exact: true })
    .getByRole("menuitem", { name: "Remove from Up next" })
    .click();
  await upNext.getByRole("button", { name: "Move Mix song 3 up" }).click();
  await upNext
    .getByRole("button", { name: "More options for Mix song 2" })
    .click();
  await page
    .getByRole("menu", { name: "Mix song 2", exact: true })
    .getByRole("menuitem", { name: "Play next" })
    .click();
  await expect
    .poll(() => queued(page))
    .toEqual(["Mix song 0", "Mix song 2", "Mix song 3", "Mix song 2"]);
  await page.keyboard.press("Escape");
  await expect(upNext).toBeHidden();
  const titles = ["Mix song 0", "Mix song 1", "Mix song 2", "Mix song 3"];
  await expect(page.locator("main .song-row strong")).toHaveText(titles);
  await page
    .getByRole("button", { name: "More options for Daily blend" })
    .click();
  await page
    .getByRole("menu", { name: "Daily blend" })
    .getByRole("menuitem", { name: "Play", exact: true })
    .click();
  await expect.poll(() => queued(page)).toEqual(titles);
});
