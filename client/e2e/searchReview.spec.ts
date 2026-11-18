import { expect, test, type Page, type Route } from "@playwright/test";
import { album, artist, mockApi } from "./mock";

// Codex round 1 on batch E (#251): the drawn Search keeps every action,
// shows the catalogue whatever the ranked search does, and opens without
// browser storage.

const nothing = { top: [], songs: [], albums: [], artists: [], playlists: [] };
const noCatalogue = (route: Route) =>
  route.fulfill({
    json: { artists: [], offset: 0, total: 0, next_offset: null },
  });

const queued = (page: Page) =>
  page.evaluate(
    () =>
      JSON.parse(localStorage.getItem("pig-music-queue") || "null")?.songs.map(
        (s: { title: string; search_play?: boolean }) =>
          `${s.title}${s.search_play ? " (search)" : ""}`,
      ) ?? [],
  );

test("a top song keeps the song menu and plays only itself", async ({
  page,
}) => {
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
  await mockApi(page, {
    answers: {
      "/v1/search/results": (route) =>
        route.fulfill({
          json: {
            ...nothing,
            top: [{ kind: "song", id: "t1" }],
            songs: album.tracks,
          },
        }),
      "/v1/catalogue/artists": noCatalogue,
    },
  });
  // Every song gets an address, so the queue is saved as it starts
  // (after mockApi: the later route wins).
  await page.route("**/api/v1/playback/*/url", (route) =>
    route.fulfill({
      json: { url: "/api/v1/playback/files/silence", expires_in: 60 },
    }),
  );
  await page.goto("/#/search?q=Von");
  const top = page.getByRole("region", { name: "Top result" });
  await expect(
    top.getByRole("button", { name: "More options for Von dutch" }),
  ).toBeVisible();
  await top.locator(".song-row").click();
  await expect.poll(() => queued(page)).toEqual(["Von dutch (search)"]);
});

test("a top album keeps its collection menu", async ({ page }) => {
  await mockApi(page, {
    answers: {
      "/v1/search/results": (route) =>
        route.fulfill({
          json: {
            ...nothing,
            top: [{ kind: "album", id: album.id }],
            albums: [{ ...album, tracks: undefined, in_library: true }],
          },
        }),
      "/v1/catalogue/artists": noCatalogue,
    },
  });
  await page.goto("/#/search?q=Brat");
  const top = page.getByRole("region", { name: "Top result" });
  await top.getByRole("button", { name: "More options for Brat" }).click();
  await expect(
    page.getByRole("menu", { name: "Brat" }).getByRole("menuitem").first(),
  ).toHaveText("Play");
});

test("the catalogue shows even when the ranked search fails", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/search/results": (route) =>
        route.fulfill({
          status: 503,
          json: { detail: "Search is busy. Try again.", code: "busy" },
        }),
      "/v1/catalogue/artists": (route) =>
        route.fulfill({
          json: {
            artists: [{ id: "mb-x", name: "Charli Band", description: "" }],
            offset: 0,
            total: 1,
            next_offset: null,
          },
        }),
    },
  });
  await page.goto("/#/search?q=Charli");
  await expect(page.getByRole("alert").first()).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Artists" }).getByRole("link", {
      name: /Charli Band/,
    }),
  ).toBeVisible();
});

test("only the same catalogue artist is folded into the server's", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/search/results": (route) =>
        route.fulfill({
          json: {
            ...nothing,
            artists: [
              { ...artist, in_library: true, musicbrainz_artist_id: "mb-1" },
            ],
          },
        }),
      "/v1/catalogue/artists": (route) =>
        route.fulfill({
          json: {
            artists: [
              { id: "mb-1", name: artist.name, description: "English singer" },
              { id: "mb-2", name: artist.name, description: "Brazilian band" },
            ],
            offset: 0,
            total: 2,
            next_offset: null,
          },
        }),
      [`/v1/catalogue/library-artists/${artist.id}`]: (route) =>
        route.fulfill({
          status: 409,
          json: {
            detail:
              "Choose this artist in catalogue search to distinguish matching names.",
            code: "conflict",
            candidates: [
              { id: "mb-1", name: artist.name, disambiguation: "singer" },
              { id: "mb-2", name: artist.name, disambiguation: "band" },
            ],
          },
        }),
    },
  });
  await page.goto("/#/search?q=Charli");
  const artists = page.getByRole("region", { name: "Artists" });
  // The namesake stays; the same artist shows once.
  await expect(artists.getByRole("link")).toHaveCount(2);
  await expect(artists).toContainText("Brazilian band");
  await expect(artists).not.toContainText("English singer");
  // An artist the server can't place offers the catalogue's choices.
  await artists.getByRole("link", { name: /in your library/ }).click();
  const choices = page.getByRole("group", { name: "Choose artist" });
  await expect(choices.getByRole("link")).toHaveCount(2);
  await expect(choices.getByRole("link").nth(1)).toHaveAttribute(
    "href",
    /#\/search\/artist\/mb-2\?/,
  );
});

test("Search opens when the browser refuses storage", async ({ page }) => {
  await mockApi(page, {
    answers: {
      "/v1/search/results": (route) => route.fulfill({ json: nothing }),
      "/v1/catalogue/artists": noCatalogue,
    },
  });
  await page.goto("/#/home");
  await expect(page.locator("main h1").first()).toBeVisible();
  // Storage goes away after start-up, as when site data is blocked.
  await page.evaluate(() =>
    Object.defineProperty(window, "localStorage", {
      get() {
        throw new DOMException("denied", "SecurityError");
      },
    }),
  );
  await page.evaluate(() => (location.hash = "#/search"));
  await expect(
    page.getByRole("searchbox", { name: "Search artists, albums or songs" }),
  ).toBeVisible();
  await expect(page.getByRole("group", { name: "Recent" })).toHaveCount(0);
});
