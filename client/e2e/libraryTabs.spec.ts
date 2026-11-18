import { expect, test } from "@playwright/test";
import { album, artist, mockApi } from "./mock";

const zebra = {
  ...album,
  id: "album-2",
  title: "Zebra",
  artist: "Aardvark",
  artist_id: "artist-2",
  year: 2001,
  cover_url: null,
  added_at: "2026-01-01T00:00:00Z",
};
const middle = {
  ...zebra,
  id: "album-3",
  title: "Middle",
  artist: "Mango",
  artist_id: "artist-3",
  year: 2010,
  added_at: null,
};
const library = {
  artists: [
    { ...artist, id: "artist-2", name: "Aardvark", album_ids: ["album-2"] },
    artist,
  ],
  // The server sends albums by artist: Aardvark before Charli xcx.
  albums: [
    zebra,
    { ...album, year: 2024, added_at: "2026-11-01T00:00:00Z" },
    middle,
  ],
  stats: {
    tracks: 4,
    artists: 2,
    albums: 2,
    compilations: 0,
    soundtracks: 0,
    musicals: 0,
  },
  snapshot: { source: "mock", captured_at: "", read_failures: [] },
};

// D-120 (#251): Library tabs carry counts; Play and Shuffle sit at the top,
// and each tab's sort names what it sorts.
test("Library tabs show counts, Play, Shuffle and a sort named for the tab", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/library": (route) => route.fulfill({ json: library }),
      "/v1/playlists": (route) =>
        route.fulfill({
          json: {
            playlists: [
              { id: "p1", name: "Road trip", track_count: 0 },
              { id: "pig-favourites:u1", name: "Favourites", track_count: 1 },
            ],
          },
        }),
      "/v1/playlists/pig-favourites:u1": (route) =>
        route.fulfill({
          json: {
            id: "pig-favourites:u1",
            name: "Favourites",
            revision: 1,
            favourites: true,
            entries: [{ ...album.tracks[0], entry_id: "e0", position: 0 }],
          },
        }),
    },
  });
  await page.goto("/#/library/albums");
  const tabs = page.getByRole("navigation", { name: "Library collections" });
  await expect(tabs.getByRole("link")).toHaveText([
    "Artists2",
    "Albums3",
    "Songs4",
    "Playlists1",
    "Favourites1",
  ]);
  const toolbar = page.locator(".library-toolbar");
  await expect(toolbar.getByRole("button", { name: "Play" })).toBeEnabled();
  await expect(toolbar.getByRole("button", { name: "Shuffle" })).toBeEnabled();
  const sort = toolbar.getByLabel("Sort");
  await expect(sort.locator("option")).toHaveText([
    "Recently added",
    "Artist A–Z",
    "Title A–Z",
    "Year",
  ]);
  // Recently added is the default: newest first, unknown times last.
  await expect(sort).toHaveValue("recent");
  const titles = page.locator(".personal-grid h3");
  await expect(titles).toHaveText(["Brat", "Zebra", "Middle"]);
  await sort.selectOption({ label: "Artist A–Z" });
  await expect(titles).toHaveText(["Zebra", "Brat", "Middle"]);
  await sort.selectOption({ label: "Title A–Z" });
  await expect(titles).toHaveText(["Brat", "Middle", "Zebra"]);
  await sort.selectOption({ label: "Year" });
  await expect(titles).toHaveText(["Brat", "Middle", "Zebra"]);

  // Play starts the library from its beginning.
  const start = page.waitForRequest((r) =>
    r.url().includes("/v1/library-pages/tracks?offset=0&limit=200"),
  );
  await toolbar.getByRole("button", { name: "Play" }).click();
  await start;

  await tabs.getByRole("link", { name: /Artists/ }).click();
  await expect(sort.locator("option")).toHaveText(["Name A–Z", "Name Z–A"]);
  await expect(titles).toHaveText(["Aardvark", "Charli xcx"]);
  await sort.selectOption({ label: "Name Z–A" });
  await expect(titles).toHaveText(["Charli xcx", "Aardvark"]);

  // Playlists and Favourites have their own Play and Shuffle.
  await tabs.getByRole("link", { name: /Playlists/ }).click();
  await expect(toolbar.getByRole("button")).toHaveCount(0);
});
