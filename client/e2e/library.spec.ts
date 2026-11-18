import { expect, test, type Route } from "@playwright/test";
import { album, artist, mockApi } from "./mock";

test("album cover loads, and a missing cover shows its lettered placeholder", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/albums/album-2": (route) =>
        route.fulfill({
          json: {
            ...album,
            id: "album-2",
            title: "Sucker",
            cover_url: "/api/v1/artwork/missing",
          },
        }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  const cover = page.locator("main .cover img").first();
  await expect(cover).toBeVisible();
  await expect
    .poll(() => cover.evaluate((img: HTMLImageElement) => img.naturalWidth))
    .toBeGreaterThan(0);

  await page.goto("/#/library/album/album-2");
  await expect(page.getByText("Von dutch").first()).toBeVisible();
  // No broken-image icon: the image is replaced by the title's placeholder.
  await expect(page.locator("main .cover").first().locator("img")).toHaveCount(
    0,
  );
  await expect(page.locator("main .cover").first()).toContainText("S");
});

const candidates = [
  { id: "mb-1", name: artist.name, disambiguation: "English singer" },
  { id: "mb-2", name: artist.name, disambiguation: "Tribute act" },
];
const ambiguous = {
  [`/v1/catalogue/library-artists/${artist.id}`]: (route: Route) =>
    route.request().method() === "PUT"
      ? route.fulfill({ json: { id: "mb-1", name: artist.name } })
      : route.fulfill({
          status: 409,
          json: {
            detail: "Several artists share this name",
            code: "conflict",
            candidates,
          },
        }),
};

test("the owner chooses which artist an ambiguous name means, once", async ({
  page,
}) => {
  await mockApi(page, { role: "owner", answers: ambiguous });
  await page.goto(`/#/library/artist/${artist.id}`);
  await page
    .getByRole("button", { name: `Find more by ${artist.name}` })
    .click();
  const choice = page.getByRole("group", { name: "Choose artist" });
  await expect(choice).toBeVisible();
  await expect(choice.getByRole("listitem")).toHaveCount(2);
  await expect(choice).toContainText("English singer");
  await expect(choice).toContainText("Tribute act");
  const put = page.waitForRequest(
    (r) => r.method() === "PUT" && r.url().includes("/library-artists/"),
  );
  await choice.getByRole("button", { name: "Choose" }).first().click();
  expect((await put).postDataJSON()).toEqual({ id: "mb-1" });
  // The catalogue page opened from the library stays under Library.
  await expect(page).toHaveURL(/#\/library\/discography\/mb-1\?/);
  await expect(page.locator(".nav-library").first()).toHaveAttribute(
    "aria-current",
    "page",
  );
});

test("library and catalogue artist pages each link to the other", async ({
  page,
}) => {
  const owned = album.tracks.map((t) => ({ ...t, cover_url: album.cover_url }));
  await mockApi(page, {
    answers: {
      [`/v1/artists/${artist.id}`]: (route) =>
        route.fulfill({
          json: {
            ...artist,
            albums: [{ ...album, year: 2024 }],
            tracks: owned,
            compilation_tracks: [],
          },
        }),
      [`/v1/catalogue/library-artists/${artist.id}`]: (route) =>
        route.fulfill({ json: { id: "mb-1", name: artist.name } }),
      "/v1/catalogue/artists/mb-1/releases": (route) =>
        route.fulfill({
          json: {
            total: 2,
            offset: 0,
            releases: [
              {
                id: "g1",
                title: "Brat",
                date: "2024-06-07",
                type: "Album",
                saved_album_id: album.id,
                saved_songs: 2,
              },
              { id: "g2", title: "Crash", date: "2022-03-18", type: "Album" },
            ],
          },
        }),
      "/v1/catalogue/groups/g1/editions": (route) =>
        route.fulfill({
          json: {
            total: 1,
            offset: 0,
            editions: [
              {
                id: "r1",
                title: "Brat",
                date: "2024-06-07",
                country: "",
                description: "",
                status: "Official",
                formats: ["Digital Media"],
                track_count: 2,
              },
            ],
          },
        }),
      "/v1/library/lookup": (route) =>
        route.fulfill({ json: { tracks: owned } }),
      "/v1/catalogue/releases/r1": (route) =>
        route.fulfill({
          json: {
            id: "r1",
            title: "Brat",
            artist: artist.name,
            artist_id: "mb-1",
            tracks: owned.map((t) => ({
              ...t,
              saved_track: t,
              server_state: "in_library",
            })),
          },
        }),
    },
  });
  // Library page: play what you have.
  await page.goto(`/#/library/artist/${artist.id}`);
  await expect(
    page.locator(".release-actions").getByRole("button", { name: "Play" }),
  ).toBeEnabled();
  await expect(page.getByRole("heading", { name: "Albums" })).toBeVisible();
  await expect(page.locator(".personal-grid")).toContainText("2024 · 2 songs");
  await expect(page.getByRole("heading", { name: "Songs" })).toBeVisible();
  await expect(page.getByText("Von dutch").first()).toBeVisible();
  // Catalogue page: find what you could have, with your copies marked.
  await page
    .getByRole("button", { name: `Find more by ${artist.name}` })
    .click();
  await expect(page).toHaveURL(/#\/library\/discography\/mb-1\?/);
  const brat = page.getByRole("button", { name: /Brat/ });
  await expect(brat).toContainText("In your library");
  await expect(page.getByRole("button", { name: /Crash/ })).not.toContainText(
    "In your library",
  );
  await brat.click();
  await expect(
    page.getByText("Already in your library: nothing to add."),
  ).toBeVisible();
  await page.getByRole("link", { name: "Open in your library" }).click();
  await expect(page).toHaveURL(new RegExp(`#/library/album/${album.id}$`));
});

test("a member is told the owner links an ambiguous artist", async ({
  page,
}) => {
  await mockApi(page, { role: "member", answers: ambiguous });
  await page.goto(`/#/library/artist/${artist.id}`);
  await page
    .getByRole("button", { name: `Find more by ${artist.name}` })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "The owner can link it" }),
  ).toBeVisible();
  await expect(page.getByRole("group", { name: "Choose artist" })).toHaveCount(
    0,
  );
});

test("a live release's songs are requested as live recordings", async ({
  page,
}) => {
  let requested: unknown;
  const live = {
    id: "t-live",
    title: "Von dutch",
    artist: artist.name,
    duration: 180,
    track_number: 1,
    disc_number: 1,
    musicbrainz_release_id: "r-live",
    server_state: "not_in_library",
    recording_kind: ["live"],
    saved_track: null,
  };
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
              {
                id: "g-live",
                title: "Concert Night",
                date: "2025",
                type: "Album",
              },
            ],
          },
        }),
      "/v1/catalogue/groups/g-live/editions": (route) =>
        route.fulfill({
          json: {
            total: 1,
            offset: 0,
            editions: [
              {
                id: "r-live",
                title: "Concert Night",
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
      "/v1/catalogue/releases/r-live": (route) =>
        route.fulfill({
          json: {
            id: "r-live",
            title: "Concert Night",
            artist: artist.name,
            artist_id: "mb-1",
            // Once added, the server reports the song as on its way.
            tracks: [requested ? { ...live, server_state: "requested" } : live],
          },
        }),
      // The studio "Von dutch" is in the library, and an edition-blind
      // lookup would say so; the release's own answer keeps the live one apart.
      "/v1/library/lookup": (route) =>
        route.fulfill({ json: { tracks: [album.tracks[0]] } }),
      "/v1/requests": (route) => {
        requested = route.request().postDataJSON();
        return route.fulfill({
          json: {
            created: 1,
            existing: 0,
            skipped: 0,
            downloads_enabled: true,
          },
        });
      },
    },
  });
  await page.goto(`/#/library/artist/${artist.id}`);
  await page
    .getByRole("button", { name: `Find more by ${artist.name}` })
    .click();
  await page.getByRole("button", { name: /Concert Night/ }).click();
  // #246: a song not on the server offers Add to library with the plus.
  const add = page.getByRole("button", {
    name: "Add Von dutch to library",
    exact: true,
  });
  await expect(add.locator("svg.lucide-plus")).toHaveCount(1);
  await page
    .getByRole("button", { name: "Add to library", exact: true })
    .click();
  await expect.poll(() => requested).toBeTruthy();
  expect(requested).toMatchObject({
    tracks: [{ title: "Von dutch", recording_kind: ["live"] }],
  });
  await expect(page.getByText("Adding 1 song to your library")).toBeVisible();
  // While the server works on it the row shows the clock and "Adding".
  await expect(
    page.getByRole("img", { name: "Adding" }).locator("svg.lucide-clock"),
  ).toHaveCount(1);
});

test("album tiles in the library name the artist", async ({ page }) => {
  await mockApi(page);
  await page.goto("/#/library/albums");
  await expect(
    page.locator(".album-card", { hasText: album.title }),
  ).toContainText(`${artist.name} · 2 songs`);
});

test("empty member pages show the pig, not bare text", async ({ page }) => {
  await mockApi(page, {
    answers: {
      "/v1/requests/groups": (route) =>
        route.fulfill({ json: { groups: [], total: 0, next_offset: null } }),
    },
  });
  for (const [hash, title] of [
    ["/#/library/playlists", "No playlists yet"],
    ["/#/library/favourites", "No favourites yet"],
    ["/#/downloads", "No downloads"],
  ]) {
    await page.goto(hash);
    const empty = page.locator(".empty-state", { hasText: title });
    await expect(empty).toBeVisible();
    await expect(empty.locator("img.empty-pig")).toHaveCount(1);
  }
});

// #97: an album whose songs are still downloading is checked less and less
// often, not every 3 seconds for as long as it stays open.
test("a catalogue album with pending songs backs off its checks", async ({
  page,
}) => {
  let reads = 0;
  await page.clock.install();
  await mockApi(page, {
    answers: {
      "/v1/catalogue/releases/r-1": (route) => {
        reads += 1;
        return route.fulfill({
          json: {
            id: "r-1",
            title: "Waiting album",
            date: "2025",
            description: "",
            tracks: [
              {
                id: "t-wait",
                title: "Still coming",
                artist: artist.name,
                duration: 180,
                track_number: 1,
                disc_number: 1,
                server_state: "requested",
              },
            ],
          },
        });
      },
    },
  });
  await page.goto(
    `/#/library/discography/mb-1?name=${encodeURIComponent(artist.name)}&release=r-1`,
  );
  await expect(page.getByText("Still coming").first()).toBeVisible();
  const first = reads;
  // A minute, a second at a time, letting each check's answer arrive.
  for (let i = 0; i < 60; i++) {
    await page.clock.runFor(1_000);
    await page.waitForTimeout(40);
  }
  expect(reads - first).toBeGreaterThan(2);
  // Every 3 s would be 20 checks a minute.
  expect(reads - first).toBeLessThanOrEqual(8);
});

// #246 (Codex): a catalogue album shows each song as the server has it
// (server_state), sends only songs that aren't yours yet, and confirms from
// the server's counts: an already queued song is being added, a refused
// one is named, and the rows are read again afterwards.
test("adding a catalogue album confirms what the server did", async ({
  page,
}) => {
  let reads = 0;
  let sent: { title: string }[] = [];
  const song = (id: string, title: string, n: number) => ({
    id,
    title,
    artist: artist.name,
    duration: 180,
    track_number: n,
    disc_number: 1,
  });
  await mockApi(page, {
    answers: {
      "/v1/catalogue/releases/r-mix": (route) => {
        reads += 1;
        return route.fulfill({
          json: {
            id: "r-mix",
            title: "Mixed album",
            artist: artist.name,
            artist_id: "mb-1",
            tracks: [
              {
                ...song("c1", "Queued song", 1),
                server_state: "requested",
              },
              {
                ...song("c2", "Skipped song", 2),
                server_state: "not_in_library",
              },
              {
                ...song("c3", "Mine already", 3),
                server_state: "in_library",
                saved_track: {
                  ...album.tracks[0],
                  id: "t9",
                  title: "Mine already",
                },
              },
            ],
          },
        });
      },
      "/v1/library/lookup": (route) =>
        route.fulfill({
          json: {
            tracks: route
              .request()
              .postDataJSON()
              .songs.map(() => null),
          },
        }),
      "/v1/requests": (route) => {
        if (route.request().method() !== "POST")
          return route.fulfill({
            json: { items: [], next_offset: null, active: 0 },
          });
        sent = route.request().postDataJSON().tracks;
        return route.fulfill({
          json: {
            created: 0,
            existing: 1,
            skipped: 1,
            downloads_enabled: true,
          },
        });
      },
    },
  });
  await page.goto(
    `/#/library/discography/mb-1?name=${encodeURIComponent(artist.name)}&release=r-mix`,
  );
  const row = (title: string) => page.locator(".song-row", { hasText: title });
  await expect(
    row("Queued song").getByRole("img", { name: "Adding" }),
  ).toBeVisible();
  await expect(
    row("Mine already").getByRole("img", { name: "In your library" }),
  ).toBeVisible();
  const before = reads;
  await page
    .getByRole("button", { name: "Add to library", exact: true })
    .click();
  await expect(
    page.getByText("Adding 1 song to your library. Skipped 1 song."),
  ).toBeVisible();
  expect(sent.map((t) => t.title)).toEqual(["Queued song", "Skipped song"]);
  await expect.poll(() => reads).toBeGreaterThan(before);
});

// #246 (Codex): an album another household member already has is added in
// one tap; its household copies are sent like missing songs.
test("a household-only catalogue album is added in one tap", async ({
  page,
}) => {
  let sent: { title: string }[] = [];
  const copy = (id: string, title: string, n: number) => ({
    id,
    title,
    artist: artist.name,
    duration: 180,
    track_number: n,
    disc_number: 1,
    server_state: "household_available",
    household_track: {
      ...album.tracks[0],
      id: "h" + id,
      title,
      server_state: "household_available",
    },
  });
  await mockApi(page, {
    answers: {
      "/v1/catalogue/releases/r-home": (route) =>
        route.fulfill({
          json: {
            id: "r-home",
            title: "Shared album",
            artist: artist.name,
            artist_id: "mb-1",
            tracks: [copy("s1", "Shared one", 1), copy("s2", "Shared two", 2)],
          },
        }),
      "/v1/library/lookup": (route) =>
        route.fulfill({
          json: {
            tracks: route
              .request()
              .postDataJSON()
              .songs.map(() => null),
          },
        }),
      "/v1/requests": (route) => {
        if (route.request().method() !== "POST")
          return route.fulfill({
            json: { items: [], next_offset: null, active: 0 },
          });
        sent = route.request().postDataJSON().tracks;
        return route.fulfill({
          json: {
            created: 0,
            existing: 2,
            skipped: 0,
            downloads_enabled: true,
          },
        });
      },
    },
  });
  await page.goto(
    `/#/library/discography/mb-1?name=${encodeURIComponent(artist.name)}&release=r-home`,
  );
  await expect(page.getByText("Shared two").first()).toBeVisible();
  await page
    .getByRole("button", { name: "Add to library", exact: true })
    .click({ timeout: 5000 });
  await expect(page.getByText("Adding 2 songs to your library.")).toBeVisible();
  expect(sent.map((t) => t.title)).toEqual(["Shared one", "Shared two"]);
});

// #246 (Codex): a song you own from another release isn't yours on this
// one when the release answer says so (explicit edition); an edition-blind
// lookup must not turn the row into "In your library" and drop it from Add.
test("a catalogue album trusts its own answer over another edition", async ({
  page,
}) => {
  let sent: { title: string }[] = [];
  await mockApi(page, {
    answers: {
      "/v1/catalogue/releases/r-deluxe": (route) =>
        route.fulfill({
          json: {
            id: "r-deluxe",
            title: "Deluxe album",
            artist: artist.name,
            artist_id: "mb-1",
            tracks: [
              {
                id: "d1",
                title: "Von dutch",
                artist: artist.name,
                duration: 180,
                track_number: 1,
                disc_number: 1,
                musicbrainz_recording_id: "rec-1",
                explicit_edition: true,
                server_state: "not_in_library",
              },
            ],
          },
        }),
      // Your copy is from the other release, with the same recording.
      "/v1/library/lookup": (route) =>
        route.fulfill({
          json: {
            tracks: [
              {
                ...album.tracks[0],
                musicbrainz_recording_id: "rec-1",
                musicbrainz_release_id: "r-standard",
              },
            ],
          },
        }),
      "/v1/requests": (route) => {
        if (route.request().method() !== "POST")
          return route.fulfill({
            json: { items: [], next_offset: null, active: 0 },
          });
        sent = route.request().postDataJSON().tracks;
        return route.fulfill({
          json: {
            created: 1,
            existing: 0,
            skipped: 0,
            downloads_enabled: true,
          },
        });
      },
    },
  });
  await page.goto(
    `/#/library/discography/mb-1?name=${encodeURIComponent(artist.name)}&release=r-deluxe`,
  );
  const row = page.locator(".song-row", { hasText: "Von dutch" });
  await expect(
    row.getByRole("button", { name: "Add Von dutch to library", exact: true }),
  ).toBeVisible();
  // Long enough for a lookup answer to have landed.
  await page.waitForTimeout(500);
  await expect(row.getByRole("img", { name: "In your library" })).toHaveCount(
    0,
  );
  await page
    .getByRole("button", { name: "Add to library", exact: true })
    .click({ timeout: 5000 });
  await expect.poll(() => sent.map((t) => t.title)).toEqual(["Von dutch"]);
});
