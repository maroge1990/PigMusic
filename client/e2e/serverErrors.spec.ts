import { expect, test, type Page, type Route } from "@playwright/test";
import { album, artist, mockApi } from "./mock";

// #239: a server fault and an unconfirmed write each say what happened.
type Devices = { phone: boolean; deletes: number };
async function devicesPage(
  page: Page,
  signOut: (route: Route, state: Devices) => unknown,
) {
  const now = Date.now() / 1000;
  const state: Devices = { phone: true, deletes: 0 };
  const session = (id: string, name: string, current = false) => ({
    id,
    kind: current ? "web" : "device",
    device_name: name,
    platform: current ? "web" : "ios",
    created_at: now - 9000,
    last_seen_at: now - 60,
    expires_at: now + 9000,
    revoked_at: null,
    current,
  });
  await mockApi(page, {
    answers: {
      "/v1/me/sessions": (route) =>
        route.fulfill({
          json: {
            sessions: [
              session("web-1", "Safari on Mac", true),
              ...(state.phone ? [session("phone-1", "iPhone")] : []),
            ],
          },
        }),
      "/v1/me/sessions/phone-1": async (route) => {
        state.deletes++;
        await signOut(route, state);
      },
      "/v1/quality": (route) =>
        route.fulfill({
          json: { preferences: [], options: [], household_default: "" },
        }),
    },
  });
  await page.goto("/#/account");
  await expect(page.locator(".session-list li")).toHaveCount(2);
  return state;
}

test("a write the server failed on shows the reference to quote", async ({
  page,
}) => {
  const state = await devicesPage(page, (route) =>
    route.fulfill({
      status: 500,
      json: {
        code: "server_error",
        detail: "Something went wrong on the server.",
        request_id: "0123abcd4567ef89",
      },
    }),
  );
  await page.getByRole("button", { name: "Sign out iPhone" }).click();
  await expect(
    page.getByText(
      "That didn't work: something went wrong on the server. Reference 0123abcd",
    ),
  ).toBeVisible();
  expect(state.deletes).toBe(1);
});

test("a write the server was too busy to confirm says so and shows what was saved", async ({
  page,
}) => {
  await devicesPage(page, (route, state) => {
    // The sign-out was in fact saved before the server ran out of time.
    state.phone = false;
    return route.fulfill({
      status: 503,
      json: {
        code: "busy_unconfirmed",
        detail:
          "The server was busy. Check whether this was saved before trying again.",
      },
    });
  });
  await page.getByRole("button", { name: "Sign out iPhone" }).click();
  await expect(
    page.getByText(
      "The server was busy, so this may or may not have been saved. Check before trying again.",
    ),
  ).toBeVisible();
  // The list is read again, so the person sees the device is gone.
  await expect(page.locator(".session-list li")).toHaveCount(1);
});

const UNCONFIRMED =
  "The server was busy, so this may or may not have been saved. Check before trying again.";

test("a Trash purge the server could not confirm doesn't claim nothing was deleted", async ({
  page,
}) => {
  const plan = {
    days: 30,
    items: [
      {
        id: "r1",
        title: "Dreams",
        artist: "Fleetwood Mac",
        album: "Rumours",
        removed_at: 1780000000,
        size_bytes: 8_400_000,
      },
    ],
    total_bytes: 8_400_000,
    fingerprint: "abc",
  };
  let purged = false;
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/library/trash": (route) =>
        route.fulfill({
          json: purged
            ? []
            : [
                {
                  id: "r1",
                  track: { title: "Dreams", artist: "Fleetwood Mac" },
                  status: "removed",
                },
              ],
        }),
      "/v1/admin/library/trash/purge": (route) => {
        if (route.request().method() !== "POST")
          return route.fulfill({ json: plan });
        // The files were deleted before the server ran out of time.
        purged = true;
        return route.fulfill({
          status: 503,
          json: {
            code: "busy_unconfirmed",
            detail:
              "The server was busy. Check whether this was saved before trying again.",
          },
        });
      },
      "/v1/admin/library/organisation/history": (route) =>
        route.fulfill({ json: [] }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
    },
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/#/admin/library");
  const panel = page
    .locator(".system-panel")
    .filter({ has: page.getByRole("heading", { name: "Removed music" }) });
  await expect(panel).toContainText("Dreams");
  await panel.getByRole("button", { name: "Review" }).click();
  await panel.getByRole("button", { name: "Delete 1 for good · 8 MB" }).click();
  await expect(page.locator(".toast")).toContainText(UNCONFIRMED);
  await expect(page.getByText("Nothing was deleted")).toHaveCount(0);
  // Trash is read again, so the owner sees the songs are gone.
  await expect(panel).toContainText("No removed music.");
});

test("an album download the proxy timed out on says it may have been requested", async ({
  page,
}) => {
  const song = {
    id: "t-new",
    title: "Club classics",
    artist: artist.name,
    duration: 180,
    track_number: 1,
    disc_number: 1,
    musicbrainz_release_id: "r-new",
    server_state: "not_in_library",
    saved_track: null,
  };
  let requests = 0;
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
                track_count: 1,
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
            tracks: [song],
          },
        }),
      "/v1/library/lookup": (route) =>
        route.fulfill({ json: { tracks: [null] } }),
      "/v1/requests": (route) => {
        if (route.request().method() !== "POST")
          return route.fulfill({
            json: { items: [], next_offset: null, active: 0 },
          });
        requests++;
        return route.fulfill({
          status: 504,
          contentType: "text/html",
          body: "<html><body>504 Gateway Time-out</body></html>",
        });
      },
    },
  });
  await page.goto(`/#/library/artist/${artist.id}`);
  await page
    .getByRole("button", { name: `Find more by ${artist.name}` })
    .click();
  await page.getByRole("button", { name: /New Night/ }).click();
  await page
    .getByRole("button", { name: "Add to library", exact: true })
    .click();
  await expect(page.getByText(UNCONFIRMED)).toBeVisible();
  expect(requests).toBe(1);
});
