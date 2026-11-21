import {
  expect,
  test,
  type BrowserContext,
  type Page,
  type Route,
} from "@playwright/test";
import { album, artist, mockApi } from "./mock";

// D-129 (#267): an album card not in your library has the one collection
// menu of D-118, as a library album's card has. A playing action looks the
// album up first (the edition its page opens on), then acts.

const collectionWords = [
  "Play",
  "Shuffle",
  "Play next",
  "Add to library",
  "Add to playlist",
  "Download",
];

const queued = (page: Page) =>
  page.evaluate(
    () =>
      JSON.parse(localStorage.getItem("pig-music-queue") || "null")?.songs.map(
        (s: { title: string }) => s.title,
      ) ?? [],
  );

const song = (
  release: string,
  title: string,
  n: number,
  saved = false,
  requested = false,
) => ({
  id: `t-${release}-${n}`,
  title,
  artist: "Someone",
  duration: 180,
  track_number: n,
  disc_number: 1,
  musicbrainz_recording_id: `rec-${release}-${n}`,
  server_state: saved
    ? "in_library"
    : requested
      ? "requested"
      : "not_in_library",
  saved_track: saved
    ? {
        id: `saved-${n}`,
        title,
        artist: "Someone",
        album: "Album one",
        duration: 180,
        server_state: "in_library",
      }
    : null,
});
const edition = (id: string) => ({
  id,
  title: "Album one",
  date: "2025",
  country: "",
  description: "",
  status: "Official",
  formats: ["Digital Media"],
  track_count: 2,
});

/** A gate the test opens: answers wait on it until `open()`. */
function gate() {
  let open: () => void = () => {};
  const shut = new Promise<void>((resolve) => (open = resolve));
  return { open, shut };
}

type Options = {
  /** Held until the test opens it: the album's editions answer. */
  editions?: Promise<void>;
  /** The edition read fails with these words. */
  failRelease?: string;
  /** The edition read fails with no words of the server's. */
  failReleaseBare?: boolean;
  /** The popular artist's popular songs (default two). */
  popular?: string[];
  /** Held until the test opens it: the artist's popular songs answer. */
  popularity?: Promise<void>;
  /** The popular songs read fails with no words of the server's. */
  failPopularity?: boolean;
  /** Held until the test opens it: the library album's read. */
  libraryAlbum?: Promise<void>;
  /** Held until the test opens it: every library lookup. */
  lookup?: Promise<void>;
  /** How many recommended albums besides Album one. */
  more?: number;
  /** Held until the test opens it: the change feed's answer, then its
   * `queue` event with these requests. */
  feed?: { open: Promise<void>; items: () => unknown[] };
  /** Held until the test opens it: Start radio's first answer. */
  radio?: Promise<void>;
  /** Titles the library lookup says are yours. */
  yours?: string[];
  /** The Add answer's outcome for each song sent (default created). */
  outcomes?: string[];
  /** Who /v1/me says is signed in. */
  who?: () => string;
};

/** Home with one recommended album (two editions: the recommended one is
 * the second, a live one is first) and one popular artist. */
async function home(target: Page | BrowserContext, options: Options = {}) {
  const added: string[][] = [];
  const listed: string[][] = [];
  // Further recommended albums: "Album k" (rg-k), its one edition rel-k
  // with "Song k.1" and "Song k.2" (recordings rec-rel-k-1 and -2).
  const more = Array.from({ length: options.more ?? 0 }, (_, i) => i + 2);
  // Where the server has each song since the test's Adds.
  const server = {
    requested: new Set<string>(),
    owned: new Set<string>(options.yours || []),
  };
  const studio = (title: string, n: number) =>
    song(
      "rel-studio",
      title,
      n,
      server.owned.has(title),
      server.requested.has(title),
    );
  const answers: Record<string, (route: Route) => Promise<void>> = {
    "/v1/home/recommended": (route) =>
      route.fulfill({
        json: {
          state: "ready",
          title: "",
          source: "",
          artists: [],
          albums: [
            {
              id: "rg-1",
              name: "Album one",
              artist: "Someone",
              artist_id: "mb-someone",
              kind: "album",
            },
            ...more.map((k) => ({
              id: `rg-${k}`,
              name: `Album ${k}`,
              artist: "Someone",
              artist_id: "mb-someone",
              kind: "album",
            })),
          ],
          songs: [],
        },
      }),
    "/v1/home/popular": (route) =>
      route.fulfill({
        json: {
          state: "ready",
          title: "Popular in Australia",
          source: "Apple Music · Australia",
          artists: [
            {
              id: "mb-pop",
              name: "Pop star",
              artist: "Pop star",
              artist_id: "mb-pop",
              kind: "artist",
            },
          ],
          albums: [],
          songs: [],
        },
      }),
    "/v1/home/recent": (route) =>
      route.fulfill({
        json: {
          albums: [{ id: album.id, title: album.title, artist: album.artist }],
        },
      }),
    "/v1/catalogue/groups/rg-1/editions": async (route) => {
      await options.editions;
      await route
        .fulfill({
          json: {
            total: 2,
            editions: [edition("rel-live"), edition("rel-studio")],
            recommended_id: "rel-studio",
          },
        })
        .catch(() => {});
    },
    "/v1/catalogue/releases/rel-live": (route) =>
      route.fulfill({
        json: {
          id: "rel-live",
          title: "Album one (Live)",
          artist: "Someone",
          date: "2025",
          description: "",
          tracks: [song("rel-live", "Live song", 1)],
        },
      }),
    "/v1/catalogue/artists/mb-pop/popularity": async (route) => {
      await options.popularity;
      await route
        .fulfill(
          options.failPopularity
            ? { status: 500, json: {} }
            : {
                json: {
                  enabled: true,
                  albums: [],
                  ranked_album_ids: [],
                  songs: (options.popular ?? ["Hit one", "Hit two"]).map(
                    (title, i) => ({
                      id: `rec-pop-${i}`,
                      title,
                      artist: "Pop star",
                      album: "Hits",
                      release_id: "rel-hits",
                    }),
                  ),
                },
              },
        )
        .catch(() => {});
    },
    "/v1/catalogue/releases/rel-studio": (route) =>
      options.failReleaseBare
        ? route.fulfill({ status: 500, json: {} })
        : options.failRelease
          ? route.fulfill({
              status: 404,
              json: { detail: options.failRelease, code: "not_found" },
            })
          : route.fulfill({
              json: {
                id: "rel-studio",
                title: "Album one",
                artist: "Someone",
                date: "2025",
                description: "",
                tracks: [studio("First song", 1), studio("Second song", 2)],
              },
            }),
    "/v1/library/lookup": async (route) => {
      await options.lookup;
      const asked = JSON.parse(route.request().postData() || "{}").songs as {
        title: string;
      }[];
      return route.fulfill({
        json: {
          tracks: asked.map((s, i) =>
            server.owned.has(s.title)
              ? song("rel-studio", s.title, i + 1, true).saved_track
              : null,
          ),
        },
      });
    },
    "/v1/requests": (route) => {
      if (route.request().method() !== "POST")
        return route.fulfill({
          json: { items: [], next_offset: null, active: 0 },
        });
      const titles: string[] = JSON.parse(
        route.request().postData() || "{}",
      ).tracks.map((t: { title: string }) => t.title);
      added.push(titles);
      const outcomes = titles.map((_, i) => options.outcomes?.[i] ?? "created");
      titles.forEach((title, i) => {
        if (["created", "existing"].includes(outcomes[i]))
          server.requested.add(title);
        if (["added", "in_library"].includes(outcomes[i]))
          server.owned.add(title);
      });
      const count = (...kinds: string[]) =>
        outcomes.filter((o) => kinds.includes(o)).length;
      return route.fulfill({
        json: {
          created: count("created"),
          existing: count("existing", "added", "in_library"),
          skipped: count("excluded"),
          outcomes,
          downloads_enabled: true,
        },
      });
    },
    "/v1/remote-playback/catalogue": (route) =>
      route.fulfill({
        json: { url: "/api/v1/playback/files/silence", source: "JioSaavn" },
      }),
    "/v1/remote-playback/prepare": (route) => route.fulfill({ json: {} }),
    [`/v1/albums/${album.id}`]: async (route) => {
      await options.libraryAlbum;
      await route.fulfill({ json: album }).catch(() => {});
    },
    "/v1/playback/t1/url": (route) =>
      route.fulfill({
        json: { url: "/api/v1/playback/files/silence", expires_in: 60 },
      }),
    "/v1/playback/t2/url": (route) =>
      route.fulfill({
        json: { url: "/api/v1/playback/files/silence", expires_in: 60 },
      }),
    "/v1/playlists": (route) =>
      route.fulfill({
        json: { playlists: [{ id: "p1", name: "Road trip", track_count: 0 }] },
      }),
    "/v1/playlists/p1": (route) =>
      route.fulfill({
        json: { id: "p1", name: "Road trip", revision: 1, entries: [] },
      }),
    "/v1/playlists/p1/songs": (route) => {
      listed.push(
        JSON.parse(route.request().postData() || "{}").tracks.map(
          (t: { title: string }) => t.title,
        ),
      );
      return route.fulfill({ json: { revision: 2 } });
    },
  };
  if (options.who) {
    const who = options.who;
    answers["/v1/me"] = (route) =>
      route.fulfill({
        json: {
          id: who(),
          username: who(),
          display_name: who() === "u1" ? "Pat" : "Sam",
          role: "member",
          session_id: who() === "u1" ? "session-1" : "session-2",
        },
      });
  }
  if (options.feed) {
    const feed = options.feed;
    answers["/v1/events"] = async (route) => {
      await feed.open;
      await route
        .fulfill({
          contentType: "text/event-stream",
          body: `retry: 600000\nevent: queue\ndata: ${JSON.stringify({ items: feed.items() })}\n\n`,
        })
        .catch(() => {});
    };
  }
  for (const k of more) {
    answers[`/v1/catalogue/groups/rg-${k}/editions`] = (route) =>
      route.fulfill({
        json: {
          total: 1,
          editions: [edition(`rel-${k}`)],
          recommended_id: `rel-${k}`,
        },
      });
    answers[`/v1/catalogue/releases/rel-${k}`] = (route) =>
      route.fulfill({
        json: {
          id: `rel-${k}`,
          title: `Album ${k}`,
          artist: "Someone",
          date: "2025",
          description: "",
          tracks: [1, 2].map((n) => song(`rel-${k}`, `Song ${k}.${n}`, n)),
        },
      });
  }
  answers["/v1/home/mixes"] = (route) =>
    route.fulfill({
      json: {
        mixes: [],
        preparing: false,
        radio: { title: "Pat's radio", ready: 2 },
      },
    });
  answers["/v1/radio"] = async (route) => {
    // Only the first answer (Start radio) waits; refills answer at once.
    const after = new URL(route.request().url()).searchParams.get("after");
    if (!after) await options.radio;
    await route
      .fulfill({
        json: {
          id: "radio-1",
          title: "Pat's radio",
          preparing: false,
          tracks: [0, 1, 2].map((i) => ({
            ...album.tracks[0],
            id: `mix:radio-1:${i}`,
            title: "Radio song " + i,
            server_state: "not_in_library",
          })),
        },
      })
      .catch(() => {});
  };
  await mockApi(target, { answers });
  await target.route("**/api/v1/playback/*/url", (route) =>
    route.fulfill({
      json: { url: "/api/v1/playback/files/silence", expires_in: 60 },
    }),
  );
  await target.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
  return { added, listed, server };
}

const card = (page: Page) =>
  page.locator(".collection-card", { hasText: "Album one" });
const dots = (page: Page) =>
  page.getByRole("button", { name: "More options for Album one" });
const choose = async (page: Page, item: string) => {
  await dots(page).click();
  await page
    .getByRole("menu", { name: "Album one" })
    .getByRole("menuitem", { name: item, exact: true })
    .click();
};

test("a recommended album card has the library album card's menu, once", async ({
  page,
}) => {
  await home(page);
  await page.goto("/#/home");
  await dots(page).click();
  const menu = page.getByRole("menu", { name: "Album one" });
  await expect(menu.getByRole("menuitem")).toHaveText(collectionWords);
  for (const item of await menu.getByRole("menuitem").all())
    await expect(item.locator("svg")).toHaveCount(1);
  await page.keyboard.press("Escape");
  // The same words, in the same order, as a library album's card.
  await page
    .getByRole("button", { name: `More options for ${album.title}` })
    .click();
  await expect(
    page.getByRole("menu", { name: album.title }).getByRole("menuitem"),
  ).toHaveText(collectionWords);
  await page.keyboard.press("Escape");
  // One ⋯ on each card: the library card's menu is not added twice.
  await expect(dots(page)).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: `More options for ${album.title}` }),
  ).toHaveCount(1);
  // A right-click on the card opens the same menu.
  await card(page).locator("button.album-card").click({ button: "right" });
  await expect(
    page.getByRole("menu", { name: "Album one" }).getByRole("menuitem"),
  ).toHaveText(collectionWords);
});

test("Play waits for the album's lookup, then plays its recommended edition in order", async ({
  page,
}) => {
  const editions = gate();
  await home(page, { editions: editions.shut });
  await page.goto("/#/home");
  await choose(page, "Play");
  // The spinner while the album is looked up; nothing plays yet.
  await expect(dots(page)).toHaveAttribute("aria-busy", "true");
  await page.waitForTimeout(300);
  expect(await queued(page)).toEqual([]);
  editions.open();
  await expect.poll(() => queued(page)).toEqual(["First song", "Second song"]);
  await expect(dots(page)).not.toHaveAttribute("aria-busy", "true");
  // Never the live edition.
  await expect(page.locator(".mini-player")).not.toContainText("Live song");
});

test("a failed lookup says so and plays nothing", async ({ page }) => {
  await home(page, { failRelease: "Release not found" });
  await page.goto("/#/home");
  await choose(page, "Play");
  await expect(page.getByText("Release not found")).toBeVisible();
  await page.waitForTimeout(300);
  expect(await queued(page)).toEqual([]);
  await expect(page.locator(".mini-player")).toBeHidden();
});

test("Add to library from the card sends the page's request and marks the card", async ({
  page,
}) => {
  const { added } = await home(page, { yours: ["First song"] });
  await page.goto("/#/home");
  await choose(page, "Add to library");
  // Only the song not yet yours, and the page's words.
  await expect.poll(() => added).toEqual([["Second song"]]);
  await expect(page.getByText("Adding 1 song to your library.")).toBeVisible();
  await expect(card(page).getByRole("img", { name: "Adding" })).toBeVisible();
  // Nothing is left to add: Add to library is off, as on the page.
  await dots(page).click();
  await expect(
    page
      .getByRole("menu", { name: "Album one" })
      .getByRole("menuitem", { name: "Add to library" }),
  ).toBeDisabled();
});

test("Add to library chosen twice while the album is looked up is sent once", async ({
  page,
}) => {
  const editions = gate();
  const { added } = await home(page, { editions: editions.shut });
  await page.goto("/#/home");
  await choose(page, "Add to library");
  await choose(page, "Add to library");
  // Play twice too: the second replaces the first, so it plays once.
  await choose(page, "Play");
  await choose(page, "Play");
  editions.open();
  await expect.poll(() => added).toEqual([["First song", "Second song"]]);
  await expect.poll(() => queued(page)).toEqual(["First song", "Second song"]);
  await page.waitForTimeout(500);
  expect(added).toHaveLength(1);
  await expect(page.locator(".notice", { hasText: "Adding" })).toHaveCount(1);
});

test("a lookup answered after another sign-in adds and plays nothing", async ({
  context,
}) => {
  let who = "u1";
  const editions = gate();
  const { added } = await home(context, {
    editions: editions.shut,
    who: () => who,
  });
  const page = await context.newPage();
  await page.goto("/#/home");
  await choose(page, "Add to library");
  await choose(page, "Play");
  // Another tab signs in as Sam while the album is looked up.
  who = "u2";
  const other = await context.newPage();
  await other.goto("/#/home");
  await other.evaluate(() => {
    const channel = new BroadcastChannel("pig-music-session");
    channel.postMessage({ change: "signed-in", tab: "other-tab", at: 1 });
    channel.close();
  });
  await expect.poll(() => page.evaluate(() => document.title)).toBeTruthy();
  await page.waitForTimeout(500);
  editions.open();
  await page.waitForTimeout(1000);
  expect(added).toEqual([]);
  expect(await queued(page)).toEqual([]);
  await expect(page.locator(".mini-player")).toBeHidden();
});

test("the card's menu works with the keyboard and gives focus back", async ({
  page,
}) => {
  await home(page);
  await page.goto("/#/home");
  await dots(page).focus();
  await page.keyboard.press("Enter");
  const menu = page.getByRole("menu", { name: "Album one" });
  await expect(
    menu.getByRole("menuitem", { name: "Play", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("menuitem", { name: "Shuffle" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(dots(page)).toBeFocused();
});

test("an artist page's album cards have the collection menu", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      [`/v1/catalogue/library-artists/${artist.id}`]: (route) =>
        route.fulfill({ json: { id: "mb-1", name: artist.name } }),
      "/v1/catalogue/artists/mb-1/releases": (route) =>
        route.fulfill({
          json: {
            total: 1,
            offset: 0,
            releases: [
              {
                id: "rg-x",
                title: "Catalogue album",
                date: "2025",
                type: "Album",
              },
            ],
          },
        }),
      "/v1/catalogue/groups/rg-x/editions": (route) =>
        route.fulfill({ json: { total: 0, editions: [] } }),
    },
  });
  await page.goto(`/#/library/artist/${artist.id}`);
  await page
    .getByRole("button", { name: `Find more by ${artist.name}` })
    .click();
  const more = page.getByRole("button", {
    name: "More options for Catalogue album",
  });
  await more.click();
  await expect(
    page.getByRole("menu", { name: "Catalogue album" }).getByRole("menuitem"),
  ).toHaveText(collectionWords);
  await page.keyboard.press("Escape");
  // Codex round 1: the ⋯ sits in the cover's corner, not across the card,
  // and the cover beside it still opens the album.
  const cardBox = (await page
    .locator(".collection-card", { hasText: "Catalogue album" })
    .boundingBox())!;
  const dotsBox = (await more.boundingBox())!;
  expect(dotsBox.width).toBeLessThan(60);
  expect(dotsBox.x).toBeGreaterThan(cardBox.x + cardBox.width / 2);
  await page.mouse.click(cardBox.x + 20, cardBox.y + 20);
  await expect(
    page.getByText("No editions of this album are available."),
  ).toBeVisible();
});

test("an album lookup that fails without the server's words says Couldn't load", async ({
  page,
}) => {
  await home(page, { failReleaseBare: true });
  await page.goto("/#/home");
  await choose(page, "Play");
  await expect(
    page.getByText("Couldn't load Album one. Try again."),
  ).toBeVisible();
  expect(await queued(page)).toEqual([]);
});

// The artist card: its songs are the artist's popular songs (D-129).
const artistDots = (page: Page) =>
  page.getByRole("button", { name: "More options for Pop star" });
const chooseForArtist = async (page: Page, item: string) => {
  await artistDots(page).click();
  await page
    .getByRole("menu", { name: "Pop star" })
    .getByRole("menuitem", { name: item, exact: true })
    .click();
};
// The artist's page has no Add to library, so the card has none either.
const artistWords = collectionWords.filter((w) => w !== "Add to library");

test("a popular artist card has the collection menu, without Add to library, once", async ({
  page,
}) => {
  await home(page);
  await page.goto("/#/home");
  await artistDots(page).click();
  const menu = page.getByRole("menu", { name: "Pop star" });
  await expect(menu.getByRole("menuitem")).toHaveText(artistWords);
  for (const item of await menu.getByRole("menuitem").all())
    await expect(item.locator("svg")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(artistDots(page)).toHaveCount(1);
  // A right-click on the card opens the same menu.
  await page
    .locator(".collection-card", { hasText: "Pop star" })
    .locator("button.album-card")
    .click({ button: "right" });
  await expect(
    page.getByRole("menu", { name: "Pop star" }).getByRole("menuitem"),
  ).toHaveText(artistWords);
});

test("Play on an artist card waits for the popular songs, then plays them in order", async ({
  page,
}) => {
  const popularity = gate();
  await home(page, { popularity: popularity.shut, yours: ["Hit two"] });
  await page.goto("/#/home");
  await chooseForArtist(page, "Play");
  await expect(artistDots(page)).toHaveAttribute("aria-busy", "true");
  await page.waitForTimeout(300);
  expect(await queued(page)).toEqual([]);
  popularity.open();
  await expect.poll(() => queued(page)).toEqual(["Hit one", "Hit two"]);
  await expect(artistDots(page)).not.toHaveAttribute("aria-busy", "true");
});

test("an artist with no popular songs says Couldn't load and plays nothing", async ({
  page,
}) => {
  await home(page, { popular: [] });
  await page.goto("/#/home");
  await chooseForArtist(page, "Play");
  await expect(
    page.getByText("Couldn't load Pop star. Try again."),
  ).toBeVisible();
  await page.waitForTimeout(300);
  expect(await queued(page)).toEqual([]);
  await expect(page.locator(".mini-player")).toBeHidden();
});

test("an artist's popular songs read that fails says Couldn't load", async ({
  page,
}) => {
  await home(page, { failPopularity: true });
  await page.goto("/#/home");
  await chooseForArtist(page, "Shuffle");
  await expect(
    page.getByText("Couldn't load Pop star. Try again."),
  ).toBeVisible();
  expect(await queued(page)).toEqual([]);
});

test("Play chosen twice on an artist card plays once", async ({ page }) => {
  const popularity = gate();
  await home(page, { popularity: popularity.shut });
  await page.goto("/#/home");
  // Play next first, then Play twice: only the last choice acts.
  await chooseForArtist(page, "Play next");
  await chooseForArtist(page, "Play");
  await chooseForArtist(page, "Play");
  popularity.open();
  await expect.poll(() => queued(page)).toEqual(["Hit one", "Hit two"]);
  await page.waitForTimeout(500);
  expect(await queued(page)).toEqual(["Hit one", "Hit two"]);
});

test("an artist's popular songs answered after another sign-in play nothing", async ({
  context,
}) => {
  let who = "u1";
  const popularity = gate();
  await home(context, { popularity: popularity.shut, who: () => who });
  const page = await context.newPage();
  await page.goto("/#/home");
  await chooseForArtist(page, "Play");
  who = "u2";
  const other = await context.newPage();
  await other.goto("/#/home");
  await other.evaluate(() => {
    const channel = new BroadcastChannel("pig-music-session");
    channel.postMessage({ change: "signed-in", tab: "other-tab", at: 1 });
    channel.close();
  });
  await page.waitForTimeout(500);
  popularity.open();
  await page.waitForTimeout(1000);
  expect(await queued(page)).toEqual([]);
  await expect(page.locator(".mini-player")).toBeHidden();
});

test("the artist card's menu works with the keyboard and gives focus back", async ({
  page,
}) => {
  await home(page);
  await page.goto("/#/home");
  await artistDots(page).focus();
  await page.keyboard.press("Enter");
  const menu = page.getByRole("menu", { name: "Pop star" });
  await expect(
    menu.getByRole("menuitem", { name: "Play", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("End");
  await expect(menu.getByRole("menuitem", { name: "Download" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(artistDots(page)).toBeFocused();
});

/** Another tab signs in as Sam. */
async function signInElsewhere(context: BrowserContext) {
  const other = await context.newPage();
  await other.goto("/#/home");
  await other.evaluate(() => {
    const channel = new BroadcastChannel("pig-music-session");
    channel.postMessage({ change: "signed-in", tab: "other-tab", at: 1 });
    channel.close();
  });
}

test("Add to playlist from the card adds the album's songs and says so", async ({
  page,
}) => {
  const { listed } = await home(page);
  await page.goto("/#/home");
  await choose(page, "Add to playlist");
  await page
    .getByRole("menu", { name: "Add Album one to playlist" })
    .getByRole("menuitem", { name: "Road trip" })
    .click();
  await expect(page.getByText("Added to playlist")).toBeVisible();
  expect(listed).toEqual([["First song", "Second song"]]);
});

// Decided in Codex round 2: once a playlist is picked, the add goes ahead
// even if the person leaves the card, and says so truly.
test("Add to playlist picked, then the card left, still adds and says so", async ({
  page,
}) => {
  const editions = gate();
  const { listed } = await home(page, { editions: editions.shut });
  await page.goto("/#/home");
  await choose(page, "Add to playlist");
  await page
    .getByRole("menu", { name: "Add Album one to playlist" })
    .getByRole("menuitem", { name: "Road trip" })
    .click();
  await page.evaluate(() => (location.hash = "#/library/albums"));
  await expect(dots(page)).toHaveCount(0);
  editions.open();
  await expect.poll(() => listed).toEqual([["First song", "Second song"]]);
  await expect(page.getByText("Added to playlist")).toBeVisible();
});

// Codex round 1: a playlist add dropped by a session change was announced.
test("Add to playlist dropped by another sign-in is neither sent nor announced", async ({
  context,
}) => {
  let who = "u1";
  const editions = gate();
  const { listed } = await home(context, {
    editions: editions.shut,
    who: () => who,
  });
  const page = await context.newPage();
  await page.goto("/#/home");
  await choose(page, "Add to playlist");
  await page
    .getByRole("menu", { name: "Add Album one to playlist" })
    .getByRole("menuitem", { name: "Road trip" })
    .click();
  who = "u2";
  await signInElsewhere(context);
  await page.waitForTimeout(500);
  const read = page.waitForResponse("**/catalogue/releases/rel-studio");
  editions.open();
  await read;
  await page.waitForTimeout(1500);
  expect(listed).toEqual([]);
  // Counted now: a notice would still be showing (it stays for seconds).
  expect(await page.getByText("Added to playlist").count()).toBe(0);
});

// Codex round 1: each card kept its own latest choice, so a slow card's
// Play could replace what another card started meanwhile.
const playFromLibraryCard = async (page: Page) => {
  await page
    .getByRole("button", { name: `More options for ${album.title}` })
    .click();
  await page
    .getByRole("menu", { name: album.title })
    .getByRole("menuitem", { name: "Play", exact: true })
    .click();
};

test("a slow catalogue card's Play never replaces what another card played since", async ({
  page,
}) => {
  const editions = gate();
  await home(page, { editions: editions.shut });
  await page.goto("/#/home");
  await choose(page, "Play");
  await playFromLibraryCard(page);
  await expect.poll(() => queued(page)).toEqual(["Von dutch", "360"]);
  const read = page.waitForResponse("**/catalogue/releases/rel-studio");
  editions.open();
  await read;
  await page.waitForTimeout(800);
  expect(await queued(page)).toEqual(["Von dutch", "360"]);
});

test("a slow library card's Play never replaces a catalogue card's played since", async ({
  page,
}) => {
  const libraryAlbum = gate();
  await home(page, { libraryAlbum: libraryAlbum.shut });
  await page.goto("/#/home");
  await playFromLibraryCard(page);
  await choose(page, "Shuffle");
  await expect
    .poll(async () => (await queued(page)).sort())
    .toEqual(["First song", "Second song"]);
  const read = page.waitForResponse(`**/api/v1/albums/${album.id}`);
  libraryAlbum.open();
  await read;
  await page.waitForTimeout(800);
  expect((await queued(page)).sort()).toEqual(["First song", "Second song"]);
});

// Codex round 1: every song sent was marked Adding, whatever the server
// did with it, and the card never looked again.
const addItem = (page: Page) =>
  page
    .getByRole("menu", { name: "Album one" })
    .getByRole("menuitem", { name: "Add to library" });

test("a song the server left out can be added again from the card", async ({
  page,
}) => {
  const { added } = await home(page, { outcomes: ["excluded", "created"] });
  await page.goto("/#/home");
  await choose(page, "Add to library");
  await expect.poll(() => added).toEqual([["First song", "Second song"]]);
  await expect(page.getByText("Adding 1 song to your library.")).toBeVisible();
  await page.waitForTimeout(500);
  await expect(card(page).getByRole("img", { name: "Adding" })).toHaveCount(0);
  await dots(page).click();
  await expect(addItem(page)).toBeEnabled();
  await addItem(page).click();
  // Only the song left out is asked for again.
  await expect.poll(() => added.at(-1)).toEqual(["First song"]);
});

test("songs the household had are marked yours at once", async ({ page }) => {
  await home(page, { outcomes: ["added", "added"] });
  await page.goto("/#/home");
  await choose(page, "Add to library");
  await expect(
    card(page).getByRole("img", { name: "In your library" }),
  ).toBeVisible();
  await expect(card(page).getByRole("img", { name: "Adding" })).toHaveCount(0);
  await dots(page).click();
  await expect(addItem(page)).toBeDisabled();
});

test("a cancelled add shows on the card, and Add is offered again", async ({
  page,
}) => {
  const { server } = await home(page);
  await page.goto("/#/home");
  await choose(page, "Add to library");
  await expect(card(page).getByRole("img", { name: "Adding" })).toBeVisible();
  // The change feed lists the two new requests, then (cancelled in
  // Downloads) neither.
  await sendQueue(page, [job("j1", 1, "waiting"), job("j2", 2, "waiting")]);
  await page.waitForTimeout(800);
  server.requested.clear();
  await sendQueue(page, []);
  await expect(card(page).getByRole("img", { name: "Adding" })).toHaveCount(0);
  await dots(page).click();
  await expect(addItem(page)).toBeEnabled();
});

/** A request in the change feed for Album one's song n (or another
 * album's, by its release). */
const job = (
  id: string,
  n: number,
  status: string,
  release = "rel-studio",
) => ({
  id,
  title:
    release === "rel-studio" ? ["First song", "Second song"][n - 1] : `Song`,
  musicbrainz_release_id: release,
  musicbrainz_recording_id: `rec-${release}-${n}`,
  status,
  received_bytes: 0,
});
/** The change feed sends this list of requests, relayed as the shell
 * relays it: the list, then the plain "requests changed". */
const sendQueue = (page: Page, items: unknown[]) =>
  page.evaluate((items) => {
    window.dispatchEvent(new CustomEvent("music-queue", { detail: { items } }));
    window.dispatchEvent(new Event("music-requests-changed"));
  }, items);

// Codex round 2: every queue change, download progress included, made
// every card that had been used read its album again.
test("only a card whose own songs' requests changed reads its album again", async ({
  page,
}) => {
  await home(page, { more: 2 });
  await page.goto("/#/home");
  // All three cards have looked their albums up (Play on each).
  for (const name of ["Album one", "Album 2", "Album 3"]) {
    await page
      .getByRole("button", { name: `More options for ${name}` })
      .click();
    await page
      .getByRole("menu", { name })
      .getByRole("menuitem", { name: "Play", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: `More options for ${name}` }),
    ).not.toHaveAttribute("aria-busy", "true");
  }
  const reads: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (/\/catalogue\/(groups|releases)\/|\/library\/lookup/.test(path))
      reads.push(path);
  });
  // Someone else's song downloading, then its progress: no card reads.
  const other = (bytes: number) => ({
    ...job("x1", 1, "downloading", "rel-other"),
    received_bytes: bytes,
  });
  await sendQueue(page, [other(10)]);
  await sendQueue(page, [other(20)]);
  await page.waitForTimeout(1200);
  expect(reads).toEqual([]);
  // A burst of changes to Album 2's songs: Album 2 alone reads, once.
  const two = (status: string, bytes = 0) => ({
    ...job("j2", 1, status, "rel-2"),
    received_bytes: bytes,
  });
  await sendQueue(page, [other(30), two("waiting")]);
  await sendQueue(page, [other(40), two("searching")]);
  await sendQueue(page, [other(50), two("downloading", 5)]);
  await expect
    .poll(() => reads, { timeout: 3000 })
    .toEqual([
      "/api/v1/catalogue/groups/rg-2/editions",
      "/api/v1/catalogue/releases/rel-2",
      "/api/v1/library/lookup",
    ]);
  // Its download progressing changes nothing more.
  await sendQueue(page, [other(60), two("downloading", 50)]);
  await sendQueue(page, [other(70), two("downloading", 90)]);
  await page.waitForTimeout(1200);
  expect(reads).toHaveLength(3);
});

// Codex round 2: a change that came while the first lookup was on its way
// was missed, and the lookup's older answer kept.
test("a request change during a card's first lookup is read again before it shows", async ({
  page,
}) => {
  const lookup = gate();
  const { server } = await home(page, { lookup: lookup.shut });
  await page.goto("/#/home");
  // Already being added before the card was first used.
  server.requested.add("First song");
  server.requested.add("Second song");
  await sendQueue(page, [job("j1", 1, "waiting"), job("j2", 2, "waiting")]);
  // The release has been read (both on their way); the library lookup,
  // the lookup's last step, waits.
  const asked = page.waitForRequest("**/api/v1/library/lookup");
  await choose(page, "Play");
  await asked;
  // Meanwhile both are cancelled in Downloads, and the feed says so.
  server.requested.clear();
  await sendQueue(page, []);
  await page.waitForTimeout(300);
  lookup.open();
  // The card reads again before it shows anything: nothing is being added.
  await page.waitForTimeout(1500);
  await expect(card(page).getByRole("img", { name: "Adding" })).toHaveCount(0);
  await dots(page).click();
  await expect(addItem(page)).toBeEnabled();
});

test("the change feed's queue event reaches the cards", async ({ page }) => {
  const feed = gate();
  let items: unknown[] = [];
  const { server } = await home(page, {
    feed: { open: feed.shut, items: () => items },
  });
  await page.goto("/#/home");
  await choose(page, "Add to library");
  await expect(card(page).getByRole("img", { name: "Adding" })).toBeVisible();
  // Cancelled elsewhere; the feed then says so.
  server.requested.clear();
  items = [{ ...job("j1", 1, "cancelled") }];
  feed.open();
  await expect(card(page).getByRole("img", { name: "Adding" })).toHaveCount(0);
});

// Codex round 2: replacing choices count from the gesture, whichever read
// finishes first; additions neither cancel nor are cancelled by them.
const startRadio = (page: Page) =>
  page.locator(".mix-card", { hasText: "Pat's radio" }).click();
const albumSongs = ["First song", "Second song"];

test("Start radio then an album's Play: the album plays, whichever answers first", async ({
  page,
}) => {
  const radio = gate();
  await home(page, { radio: radio.shut });
  await page.goto("/#/home");
  await startRadio(page);
  await choose(page, "Play");
  await expect.poll(() => queued(page)).toEqual(albumSongs);
  const answered = page.waitForResponse("**/api/v1/radio");
  radio.open();
  await answered;
  await page.waitForTimeout(800);
  expect(await queued(page)).toEqual(albumSongs);
});

test("an album's Play then Start radio: the radio plays, whichever answers first", async ({
  page,
}) => {
  const editions = gate();
  await home(page, { editions: editions.shut });
  await page.goto("/#/home");
  await choose(page, "Play");
  await startRadio(page);
  await expect.poll(async () => (await queued(page))[0]).toBe("Radio song 0");
  const read = page.waitForResponse("**/catalogue/releases/rel-studio");
  editions.open();
  await read;
  await page.waitForTimeout(800);
  const now = await queued(page);
  expect(now[0]).toBe("Radio song 0");
  expect(now).not.toContain("First song");
});

test("Play next while an album's Play is on its way goes into the queue playing; the Play still lands", async ({
  page,
}) => {
  const editions = gate();
  await home(page, { editions: editions.shut });
  await page.goto("/#/home");
  await playFromLibraryCard(page);
  await expect.poll(() => queued(page)).toEqual(["Von dutch", "360"]);
  await choose(page, "Play");
  // Play next on the library card: added to what is playing now.
  await page
    .getByRole("button", { name: `More options for ${album.title}` })
    .click();
  await page
    .getByRole("menu", { name: album.title })
    .getByRole("menuitem", { name: "Play next" })
    .click();
  await expect
    .poll(() => queued(page))
    .toEqual(["Von dutch", "Von dutch", "360", "360"]);
  // The album's Play then replaces that queue, the addition with it.
  editions.open();
  await expect.poll(() => queued(page)).toEqual(albumSongs);
});

test("Play next on its way when another Play lands goes into the new queue", async ({
  page,
}) => {
  const editions = gate();
  await home(page, { editions: editions.shut });
  await page.goto("/#/home");
  await choose(page, "Play next");
  await playFromLibraryCard(page);
  await expect.poll(() => queued(page)).toEqual(["Von dutch", "360"]);
  editions.open();
  await expect
    .poll(() => queued(page))
    .toEqual(["Von dutch", "First song", "Second song", "360"]);
});
