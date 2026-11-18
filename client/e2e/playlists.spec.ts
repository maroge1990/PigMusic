import { expect, test, type Route } from "@playwright/test";
import { album, mockApi } from "./mock";

const entries = (count: number, covers = 1) =>
  Array.from({ length: count }, (_, position) => ({
    ...album.tracks[position % 2],
    id: "t" + position,
    title: "Song " + position,
    cover_url: `/api/v1/artwork/cover-${(position % covers) + 1}`,
    duration: 1380,
    entry_id: "e" + position,
    position,
  }));
const favourites = {
  id: "pig-favourites:u1",
  name: "Favourites",
  revision: 1,
  favourites: true,
  entries: entries(3),
};
const roadTrip = { id: "p1", name: "Road trip", revision: 4, entries: [] };
const kitchen = {
  id: "p2",
  name: "Kitchen disco",
  revision: 1,
  entries: entries(4, 4),
};
const json = (body: unknown) => (route: Route) => route.fulfill({ json: body });

async function playlists(page: Parameters<typeof mockApi>[0]) {
  const created: string[] = [];
  await mockApi(page, {
    answers: {
      // The server lists Favourites wherever it falls; it shows first.
      "/v1/playlists": (route) => {
        if (route.request().method() === "POST") {
          created.push(route.request().postData() || "");
          return route.fulfill({ json: { ...roadTrip, id: "p3" } });
        }
        return route.fulfill({
          json: {
            playlists: [
              { id: "p1", name: "Road trip", track_count: 0 },
              { id: favourites.id, name: "Favourites", track_count: 3 },
              { id: "p2", name: "Kitchen disco", track_count: 4 },
            ],
          },
        });
      },
      "/v1/playlists/p1": json(roadTrip),
      "/v1/playlists/p2": json(kitchen),
      ["/v1/playlists/" + favourites.id]: json(favourites),
    },
  });
  return created;
}

// D-120 (#251): playlists are covers with their count and length,
// Favourites first and a New playlist card last.
test("the Playlists tab shows Favourites first, then covers with count and length", async ({
  page,
}) => {
  const created = await playlists(page);
  await page.goto("/#/library/playlists");
  const grid = page.locator(".playlist-grid");
  await expect(grid.locator("h3, .grid-card-title")).toHaveText([
    "Favourites",
    "Road trip",
    "Kitchen disco",
    "New playlist",
  ]);
  await expect(grid.locator(".album-card").first()).toContainText(
    "3 songs · 1 hr 9 min",
  );
  const kitchenCard = grid.locator(".album-card", {
    hasText: "Kitchen disco",
  });
  await expect(kitchenCard).toContainText("4 songs · 1 hr 32 min");
  await expect(kitchenCard.locator(".playlist-mosaic .cover")).toHaveCount(4);
  await expect(
    grid.locator(".album-card", { hasText: "Road trip" }),
  ).toContainText("0 songs");
  await expect(page.locator(".empty-state")).toHaveCount(0);

  await page.getByRole("button", { name: "New playlist" }).click();
  const dialog = page.getByRole("dialog", { name: "New playlist" });
  await dialog.getByLabel("Playlist name").fill("Sunday morning");
  await dialog.getByRole("button", { name: "Create playlist" }).click();
  await expect(dialog).toBeHidden();
  expect(created.map((body) => JSON.parse(body))).toEqual([
    { name: "Sunday morning" },
  ]);
});

// D-120 (#251): a playlist page leads with its covers, count and length,
// then Play, Shuffle and its ⋯.
test("a playlist page shows its count and length with Play, Shuffle and more", async ({
  page,
}) => {
  await playlists(page);
  await page.goto("/#/library/playlist/p2");
  const header = page.locator(".page-header");
  await expect(header.locator("h1")).toHaveText("Kitchen disco");
  await expect(header).toContainText("4 songs · 1 hr 32 min");
  await expect(header.locator(".playlist-mosaic .cover")).toHaveCount(4);
  const actions = page.locator(".release-actions");
  await expect(actions.getByRole("button", { name: "Play" })).toBeEnabled();
  await expect(
    actions.getByRole("button", { name: "Shuffle", exact: true }),
  ).toHaveText("Shuffle");
  await actions
    .getByRole("button", { name: "More options for Kitchen disco" })
    .click();
  await expect(
    page.getByRole("menu", { name: "Kitchen disco" }).getByRole("menuitem"),
  ).toHaveText([
    "Play",
    "Shuffle",
    "Play next",
    "Add to library",
    "Add to playlist",
    "Download",
  ]);
});

// D-120 (#251): Favourites has its star, count and length, Play, Shuffle and
// a Sort that starts with the newest.
test("Favourites shows its count and length with Play, Shuffle and Sort", async ({
  page,
}) => {
  await playlists(page);
  await page.goto("/#/library/favourites");
  const header = page.locator(".list-header");
  await expect(header.locator("h2")).toHaveText("Favourites");
  await expect(header).toContainText("3 songs · 1 hr 9 min");
  await expect(header.getByRole("button", { name: "Play" })).toBeEnabled();
  await expect(
    header.getByRole("button", { name: "Shuffle", exact: true }),
  ).toBeEnabled();
  const titles = page.locator(".song-list .song-text strong");
  await expect(titles).toHaveText(["Song 2", "Song 1", "Song 0"]);
  const sort = header.getByLabel("Sort");
  await expect(sort.locator("option")).toHaveText([
    "Recently added",
    "Title",
    "Artist",
  ]);
  await sort.selectOption({ label: "Title" });
  await expect(titles).toHaveText(["Song 0", "Song 1", "Song 2"]);
});

// Codex round 1, finding 6: a playlist edited without changing its count
// (one song swapped for another) shows its new length, not the old one.
test("a playlist card follows an edit that keeps its count", async ({
  page,
}) => {
  let edited = false;
  const swapped = {
    ...kitchen,
    revision: 2,
    entries: kitchen.entries.map((e) => ({ ...e, duration: 60 })),
  };
  await mockApi(page, {
    answers: {
      "/v1/playlists": (route) =>
        route.fulfill({
          json: {
            playlists: [
              {
                id: "p2",
                name: "Kitchen disco",
                track_count: 4,
                revision: edited ? 2 : 1,
              },
            ],
          },
        }),
      "/v1/playlists/p2": (route) =>
        route.fulfill({ json: edited ? swapped : kitchen }),
    },
  });
  await page.goto("/#/library/playlists");
  const card = page.locator(".playlist-grid .album-card", {
    hasText: "Kitchen disco",
  });
  await expect(card).toContainText("4 songs · 1 hr 32 min");
  edited = true;
  await page.evaluate(() =>
    window.dispatchEvent(new Event("music-library-changed")),
  );
  await expect(card).toContainText("4 songs · 4 min");
});

// Codex round 2: a failed read of an edited playlist shows a plain card,
// not the old length, and the next refresh reads it again.
test("a playlist card never shows an older version's length", async ({
  page,
}) => {
  let revision = 1;
  let failing = false;
  const swapped = {
    ...kitchen,
    revision: 2,
    entries: kitchen.entries.map((e) => ({ ...e, duration: 60 })),
  };
  await mockApi(page, {
    answers: {
      "/v1/playlists": (route) =>
        route.fulfill({
          json: {
            playlists: [
              { id: "p2", name: "Kitchen disco", track_count: 4, revision },
            ],
          },
        }),
      "/v1/playlists/p2": (route) =>
        failing
          ? route.fulfill({
              status: 503,
              json: { detail: "Busy", code: "busy" },
            })
          : route.fulfill({ json: revision === 1 ? kitchen : swapped }),
    },
  });
  await page.goto("/#/library/playlists");
  const card = page.locator(".playlist-grid .album-card", {
    hasText: "Kitchen disco",
  });
  await expect(card).toContainText("4 songs · 1 hr 32 min");
  revision = 2;
  failing = true;
  const changed = () =>
    page.evaluate(() =>
      window.dispatchEvent(new Event("music-library-changed")),
    );
  await changed();
  await expect(card.locator("p")).toHaveText("4 songs");
  failing = false;
  await changed();
  await expect(card).toContainText("4 songs · 4 min");
});
