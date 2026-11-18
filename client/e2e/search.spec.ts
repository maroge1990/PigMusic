import { expect, test, type Route } from "@playwright/test";
import { album, artist, mockApi } from "./mock";

const nothing = { top: [], songs: [], albums: [], artists: [], playlists: [] };

test("search types ahead without a request per keystroke", async ({ page }) => {
  const asked: string[] = [];
  await mockApi(page, {
    answers: {
      "/v1/search/results": (route) => {
        asked.push(
          "library:" + new URL(route.request().url()).searchParams.get("q"),
        );
        return route.fulfill({
          json: {
            ...nothing,
            top: [{ kind: "artist", id: artist.id }],
            artists: [
              {
                ...artist,
                in_library: true,
                musicbrainz_artist_id: "mb-charli",
              },
            ],
          },
        });
      },
      "/v1/catalogue/artists": (route) => {
        const q = new URL(route.request().url()).searchParams.get("q") || "";
        asked.push("catalogue:" + q);
        return route.fulfill({
          json: {
            artists: [
              {
                id: "mb-charli",
                name: artist.name,
                description: "English singer",
              },
              { id: "mb-other", name: q + " Band", description: "" },
            ],
            offset: 0,
            total: 2,
            next_offset: null,
          },
        });
      },
    },
  });
  await page.goto("/#/search");
  const box = page.getByRole("searchbox", {
    name: "Search artists, albums or songs",
  });
  await box.pressSequentially("Charli", { delay: 40 });
  const discover = page.locator(".discover-list");
  await expect(discover).toContainText("Charli Band");
  // One pause, one search: nothing was sent for "C", "Ch", "Cha" …
  expect(asked).toEqual(["library:Charli", "catalogue:Charli"]);
  await expect(page).toHaveURL(/#\/search\?q=Charli$/);
  // Catalogue rows show artwork and type; an artist the server already has
  // is shown once, as the top result, never again from the catalogue.
  const band = discover.getByRole("link", { name: /Charli Band/ });
  await expect(band).toContainText("Artist");
  await expect(band.locator(".cover")).toHaveCount(1);
  await expect(page.locator(".search-top-card")).toContainText(artist.name);
  await expect(page.getByText("English singer")).toHaveCount(0);
  // Changing and going back reuses the earlier answers.
  await box.press("Backspace");
  await expect(discover).toContainText("Charl Band");
  await box.press("i");
  await expect(discover).toContainText("Charli Band");
  expect(asked).toEqual([
    "library:Charli",
    "catalogue:Charli",
    "library:Charl",
    "catalogue:Charl",
  ]);
  // One letter searches your library only.
  await box.fill("C");
  await expect(
    page.getByText("Type another letter to search the catalogue."),
  ).toBeVisible();
  expect(asked.filter((a) => a === "catalogue:C")).toEqual([]);
});

test("signing in as someone else never shows the previous person's search", async ({
  page,
}) => {
  let person: string | null = "pat";
  const asked: string[] = [];
  await mockApi(page, {
    answers: {
      // The live feed never answers, so no library revision clears anything.
      "/v1/events": () => new Promise(() => {}),
      "/v1/me": (route) =>
        person
          ? route.fulfill({
              json: {
                id: person,
                username: person,
                display_name: person,
                role: "member",
              },
            })
          : route.fulfill({
              status: 401,
              json: {
                detail: "Please sign in to continue.",
                code: "authentication_required",
              },
            }),
      "/v1/auth/logout": (route) => {
        person = null;
        return route.fulfill({ json: {} });
      },
      "/v1/auth/login": (route) => {
        person = JSON.parse(route.request().postData() || "{}").username;
        return route.fulfill({ json: {} });
      },
      "/v1/me/sessions": (route) => route.fulfill({ json: { sessions: [] } }),
      "/v1/quality": (route) =>
        route.fulfill({
          json: { preferences: [], options: [], household_default: "" },
        }),
      "/v1/search/results": (route) => {
        asked.push(person || "nobody");
        return route.fulfill({
          json: {
            ...nothing,
            songs: [
              {
                id: `${person}-song`,
                title: `${person}'s saved song`,
                artist: "Charli xcx",
                album: "Brat",
                album_id: "album-1",
                duration: 180,
                server_state: "in_library",
              },
            ],
          },
        });
      },
      "/v1/catalogue/artists": (route) =>
        route.fulfill({
          json: { artists: [], offset: 0, total: 0, next_offset: null },
        }),
    },
  });
  await page.goto("/#/search?q=Charli");
  await expect(page.getByText("pat's saved song")).toBeVisible();
  await page.goto("/#/account");
  await page.getByRole("button", { name: "Sign out of PigMusic" }).click();
  await page.getByLabel("Username").fill("sam");
  await page.getByLabel("Password").fill("generated e2e password");
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.goto("/#/search?q=Charli");
  await expect(page.getByText("sam's saved song")).toBeVisible();
  await expect(page.getByText("pat's saved song")).toHaveCount(0);
  expect(asked).toEqual(["pat", "sam"]);
});

test("#182: the library answers 150 ms after typing; the catalogue waits for a longer pause", async ({
  page,
}) => {
  await page.clock.install();
  const asked: string[] = [];
  const record = (kind: string) => (route: Route) => {
    asked.push(
      kind + ":" + new URL(route.request().url()).searchParams.get("q"),
    );
    return route.fulfill({
      json:
        kind === "library"
          ? nothing
          : { artists: [], offset: 0, total: 0, next_offset: null },
    });
  };
  await mockApi(page, {
    answers: {
      "/v1/search/results": record("library"),
      "/v1/catalogue/artists": record("catalogue"),
    },
  });
  await page.goto("/#/search");
  const box = page.getByRole("searchbox", {
    name: "Search artists, albums or songs",
  });
  await expect(box).toBeVisible();
  // Time moves only when the test says so.
  await page.clock.pauseAt(Date.now() + 60_000);
  await box.pressSequentially("Cha");
  await page.clock.runFor(160);
  await expect.poll(() => asked).toEqual(["library:Cha"]);
  // A 250 ms pause between words is not long enough to ask the catalogue.
  await page.clock.runFor(90);
  await box.pressSequentially("r");
  await page.clock.runFor(160);
  await expect.poll(() => asked).toEqual(["library:Cha", "library:Char"]);
  await page.clock.runFor(250);
  await expect
    .poll(() => asked)
    .toEqual(["library:Cha", "library:Char", "catalogue:Char"]);
});

test("D-120: results are grouped and each item shows once with one mark", async ({
  page,
}) => {
  const server = (id: string, title: string, state: string, mine: boolean) => ({
    ...album,
    id,
    title,
    tracks: undefined,
    server_state: state,
    in_library: mine,
  });
  await mockApi(page, {
    answers: {
      "/v1/search/results": (route) =>
        route.fulfill({
          json: {
            ...nothing,
            top: [
              { kind: "album", id: album.id },
              { kind: "song", id: "t1" },
            ],
            songs: album.tracks,
            // The album is both in the library and the top result.
            albums: [
              server(album.id, "Brat", "in_library", true),
              server("album-2", "Brat remixed", "requested", true),
              server("album-3", "Sucker", "household_available", false),
            ],
            artists: [
              {
                ...artist,
                in_library: true,
                musicbrainz_artist_id: "mb-charli",
              },
            ],
          },
        }),
      "/v1/catalogue/artists": (route) =>
        route.fulfill({
          json: {
            artists: [{ id: "mb-charli", name: artist.name }],
            offset: 0,
            total: 1,
            next_offset: null,
          },
        }),
    },
  });
  await page.goto("/#/search?q=Brat");
  const results = page.locator(".search-results");
  await expect(results.locator("h2")).toHaveText([
    "Top result",
    "Songs",
    "Albums",
    "Artists",
  ]);
  // Brat is the top result and nowhere else.
  await expect(page.locator(".search-top-card")).toContainText("Brat");
  await expect(
    results.locator("a.album-card", { hasText: /^Brat$/ }),
  ).toHaveCount(0);
  const albums = page.getByRole("region", { name: "Albums" });
  await expect(albums.locator("a.album-card")).toHaveCount(2);
  await expect(
    albums
      .locator("a.album-card", { hasText: "Brat remixed" })
      .getByRole("img", {
        name: "Adding",
      }),
  ).toHaveCount(1);
  await expect(
    albums.locator("a.album-card", { hasText: "Sucker" }).getByRole("img", {
      name: "Not in your library",
    }),
  ).toHaveCount(1);
  // One mark per item, and the artist once though the catalogue has it too.
  for (const card of await albums.locator("a.album-card").all())
    await expect(card.locator(".state-badge")).toHaveCount(1);
  const artists = page.getByRole("region", { name: "Artists" });
  await expect(artists.getByRole("link")).toHaveCount(1);
  await expect(artists).toContainText("1 album in your library");
  await expect(
    page.getByRole("region", { name: "Songs" }).locator(".song-row"),
  ).toHaveCount(2);
});

test("D-120: before typing, recent searches, your library and Recently added", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/search/results": (route) => route.fulfill({ json: nothing }),
      "/v1/catalogue/artists": (route) =>
        route.fulfill({
          json: { artists: [], offset: 0, total: 0, next_offset: null },
        }),
      "/v1/home/recent": (route) =>
        route.fulfill({
          json: {
            albums: [
              {
                id: album.id,
                title: "Brat",
                artist: artist.name,
                cover_url: album.cover_url,
              },
            ],
          },
        }),
    },
  });
  await page.goto("/#/search");
  const shortcuts = page.getByRole("region", { name: "Your library" });
  await expect(shortcuts.getByRole("link")).toHaveText([
    /Artists\s*1/,
    /Albums\s*1/,
    /Songs\s*2/,
    /Playlists\s*0/,
  ]);
  await expect(shortcuts.getByRole("link", { name: /Albums/ })).toHaveAttribute(
    "href",
    "#/library/albums",
  );
  const added = page.getByRole("region", { name: "Recently added" });
  await expect(added.locator(".album-card img")).toHaveCount(1);
  await expect(page.getByRole("group", { name: "Recent" })).toHaveCount(0);
  // A search kept with Enter comes back under Recent.
  const box = page.getByRole("searchbox", {
    name: "Search artists, albums or songs",
  });
  await box.fill("charli");
  await box.press("Enter");
  await expect(page.getByText(/Nothing matches/)).toBeVisible();
  await page.goto("/#/search");
  const recent = page.getByRole("group", { name: "Recent" });
  await expect(recent.getByRole("link", { name: "charli" })).toBeVisible();
  await recent.getByRole("button", { name: "Clear" }).click();
  await expect(recent).toHaveCount(0);
});
