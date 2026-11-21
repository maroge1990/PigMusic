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

const song = (release: string, title: string, n: number, saved = false) => ({
  id: `t-${release}-${n}`,
  title,
  artist: "Someone",
  duration: 180,
  track_number: n,
  disc_number: 1,
  musicbrainz_recording_id: `rec-${release}-${n}`,
  server_state: saved ? "in_library" : "not_in_library",
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
  /** Titles the library lookup says are yours. */
  yours?: string[];
  /** Who /v1/me says is signed in. */
  who?: () => string;
};

/** Home with one recommended album (two editions: the recommended one is
 * the second, a live one is first) and one popular artist. */
async function home(target: Page | BrowserContext, options: Options = {}) {
  const added: string[][] = [];
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
    "/v1/catalogue/releases/rel-studio": (route) =>
      options.failRelease
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
              tracks: [
                song("rel-studio", "First song", 1),
                song("rel-studio", "Second song", 2),
              ],
            },
          }),
    "/v1/library/lookup": (route) => {
      const asked = JSON.parse(route.request().postData() || "{}").songs as {
        title: string;
      }[];
      return route.fulfill({
        json: {
          tracks: asked.map((s, i) =>
            options.yours?.includes(s.title)
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
      added.push(
        JSON.parse(route.request().postData() || "{}").tracks.map(
          (t: { title: string }) => t.title,
        ),
      );
      return route.fulfill({
        json: { created: added.at(-1)!.length, existing: 0, skipped: 0 },
      });
    },
    "/v1/remote-playback/catalogue": (route) =>
      route.fulfill({
        json: { url: "/api/v1/playback/files/silence", source: "JioSaavn" },
      }),
    "/v1/remote-playback/prepare": (route) => route.fulfill({ json: {} }),
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
  await mockApi(target, { answers });
  await target.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
  return { added };
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
    },
  });
  await page.goto(`/#/library/artist/${artist.id}`);
  await page
    .getByRole("button", { name: `Find more by ${artist.name}` })
    .click();
  await page
    .getByRole("button", { name: "More options for Catalogue album" })
    .click();
  await expect(
    page.getByRole("menu", { name: "Catalogue album" }).getByRole("menuitem"),
  ).toHaveText(collectionWords);
});
