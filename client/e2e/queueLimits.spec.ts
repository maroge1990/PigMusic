import { expect, test, type Page, type Route } from "@playwright/test";
import { album, artist, mockApi } from "./mock";

// D-128, #242: an album Add past a queue limit says how many songs were
// left out, whether some fit (200) or none did (429 queue_limit).
async function albumPage(page: Page, add: (route: Route) => unknown) {
  const song = (n: number) => ({
    id: "t-" + n,
    title: "Club classics " + n,
    artist: artist.name,
    duration: 180,
    track_number: n,
    disc_number: 1,
    musicbrainz_release_id: "r-new",
    server_state: "not_in_library",
    saved_track: null,
  });
  await mockApi(page, {
    answers: {
      [`/v1/artists/${artist.id}`]: (route) =>
        route.fulfill({
          json: {
            ...artist,
            albums: [album],
            tracks: [],
            compilation_tracks: [],
          },
        }),
      [`/v1/catalogue/library-artists/${artist.id}`]: (route) =>
        route.fulfill({ json: { id: "mb-1", name: artist.name } }),
      "/v1/catalogue/artists/mb-1/releases": (route) =>
        route.fulfill({
          json: {
            total: 1,
            offset: 0,
            releases: [
              { id: "g-new", title: "New Night", date: "2025", type: "Album" },
            ],
          },
        }),
      "/v1/catalogue/groups/g-new/editions": (route) =>
        route.fulfill({
          json: {
            total: 1,
            offset: 0,
            editions: [
              {
                id: "r-new",
                title: "New Night",
                date: "2025",
                country: "",
                description: "",
                status: "Official",
                formats: ["Digital Media"],
                track_count: 3,
              },
            ],
          },
        }),
      "/v1/catalogue/releases/r-new": (route) =>
        route.fulfill({
          json: {
            id: "r-new",
            title: "New Night",
            artist: artist.name,
            artist_id: "mb-1",
            tracks: [song(1), song(2), song(3)],
          },
        }),
      "/v1/library/lookup": (route) =>
        route.fulfill({ json: { tracks: [null, null, null] } }),
      "/v1/requests": (route) =>
        route.request().method() === "POST"
          ? add(route)
          : route.fulfill({
              json: { items: [], next_offset: null, active: 0 },
            }),
    },
  });
  await page.goto(`/#/library/artist/${artist.id}`);
  await page
    .getByRole("button", { name: `Find more by ${artist.name}` })
    .click();
  // The card itself, not its menu (D-129).
  await page.getByRole("button", { name: /^New Night/ }).click();
  await page
    .getByRole("button", { name: "Add to library", exact: true })
    .click();
}

test("an album Add past the rate says how many were left out", async ({
  page,
}) => {
  await albumPage(page, (route) =>
    route.fulfill({
      json: {
        created: 1,
        existing: 0,
        skipped: 0,
        paused: 0,
        paused_ids: [],
        left_out: 2,
        limit: "rate",
        retry_after: 90,
        outcomes: ["created", "excluded", "excluded"],
        over_limit: [1, 2],
        downloads_enabled: true,
      },
    }),
  );
  await expect(page.locator(".notice-area")).toContainText(
    "Adding 1 song to your library. Couldn't add 2 songs: too many are waiting. Try again later.",
  );
});

test("an album Add with every song past the cap says so", async ({ page }) => {
  await albumPage(page, (route) =>
    route.fulfill({
      status: 429,
      json: {
        detail: "Too many songs are waiting. Try again later.",
        code: "queue_limit",
        created: 0,
        existing: 0,
        skipped: 0,
        left_out: 3,
        limit: "person_waiting",
        retry_after: null,
        outcomes: ["excluded", "excluded", "excluded"],
        over_limit: [0, 1, 2],
        downloads_enabled: true,
      },
    }),
  );
  await expect(
    page.getByText(
      "Couldn't add 3 songs: too many are waiting. Try again later.",
    ),
  ).toBeVisible();
});

test("a Retry in Downloads a waiting limit refused says why", async ({
  page,
}) => {
  const item = {
    id: "f1",
    title: "Faint",
    artist: "Linkin Park",
    status: "failed",
    request_progress: null,
    error: "no_source",
    source: null,
    quality: null,
  };
  const groups = {
    total: 1,
    next_offset: null,
    active: 0,
    offset: 0,
    downloads_enabled: true,
    groups: [
      {
        id: "g-faint",
        kind: "song",
        title: "Faint",
        artist: "Linkin Park",
        release_id: null,
        state: "failed",
        songs: 1,
        done: 0,
        active: 0,
        waiting: 0,
        failed: 1,
        progress: 0,
        requested_at: "2026-10-01T00:00:00Z",
        updated_at: "2026-10-01T00:00:00Z",
        items: [item],
      },
    ],
  };
  await mockApi(page, {
    answers: {
      "/v1/requests/groups": (route) => route.fulfill({ json: groups }),
      "/v1/requests/f1/retry": (route) =>
        route.fulfill({
          status: 429,
          json: {
            detail: "Too many songs are waiting. Try again later.",
            code: "queue_limit",
            limit: "person_waiting",
            retry_after: null,
            left_out: 1,
          },
        }),
      "/v1/requests": (route) =>
        route.fulfill({ json: { items: [], next_offset: null, active: 0 } }),
    },
  });
  await page.goto("/#/downloads");
  await page
    .getByRole("button", { name: /Try again/ })
    .first()
    .click();
  await expect(
    page.getByText("Too many songs are waiting. Try again later."),
  ).toBeVisible();
});
