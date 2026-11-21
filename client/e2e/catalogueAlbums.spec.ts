import { expect, test, type Page, type Route } from "@playwright/test";
import { album, artist, mockApi } from "./mock";

// Catalogue albums: an answer that arrives late belongs to the album or
// edition it was asked for, never to the one now on screen (#262, #263).

type Release = {
  id: string;
  title: string;
  song: string;
  explicit?: boolean;
  /** More songs after the first. */
  more?: string[];
  /** The songs the server says are being added, by read number. */
  requested?: (read: number) => string[];
  /** The songs in your library, by read number. */
  saved?: (read: number) => string[];
};
const track = (r: Release, title: string, n: number, read: number) => ({
  id: `t-${r.id}-${n}`,
  title,
  artist: artist.name,
  album: r.title,
  duration: 180,
  track_number: n,
  disc_number: 1,
  musicbrainz_release_id: r.id,
  musicbrainz_recording_id: `rec-${r.id}-${n}`,
  explicit_edition: !!r.explicit,
  server_state: r.saved?.(read).includes(title)
    ? "in_library"
    : r.requested?.(read).includes(title)
      ? "requested"
      : "not_in_library",
  saved_track: r.saved?.(read).includes(title)
    ? {
        id: `saved-${r.id}-${n}`,
        title,
        artist: artist.name,
        album: r.title,
        duration: 180,
        server_state: "in_library",
      }
    : null,
});
const edition = (r: Release) => ({
  id: r.id,
  title: r.title,
  date: "2025",
  country: "",
  description: "",
  status: "Official",
  formats: ["Digital Media"],
  track_count: 1,
});

/** A library artist whose catalogue has these album groups, each with its
 * editions (the first is the recommended one). `hold(id)` delays every
 * read of that release after the first until `release()` is called. */
async function catalogue(
  page: Page,
  groups: { id: string; title: string; editions: Release[] }[],
  add: (route: Route) => Promise<void>,
  lookup: (route: Route) => Promise<void> = (route) =>
    route.fulfill({
      json: {
        tracks: JSON.parse(route.request().postData() || "{}").songs.map(
          () => null,
        ),
      },
    }),
) {
  const releases = new Map(
    groups.flatMap((g) => g.editions).map((r) => [r.id, r]),
  );
  const reads = new Map<string, number>();
  const held = new Set<string>();
  const waiting: { n: number; resolve: () => void }[] = [];
  const answers: Record<string, (route: Route) => Promise<void>> = {
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
          total: groups.length,
          offset: 0,
          releases: groups.map((g) => ({
            id: g.id,
            title: g.title,
            date: "2025",
            type: "Album",
          })),
        },
      }),
    "/v1/library/lookup": lookup,
    "/v1/requests": (route) =>
      route.request().method() === "POST"
        ? add(route)
        : route.fulfill({ json: { items: [], next_offset: null, active: 0 } }),
  };
  for (const g of groups)
    answers[`/v1/catalogue/groups/${g.id}/editions`] = (route) =>
      route.fulfill({
        json: {
          total: g.editions.length,
          offset: 0,
          editions: g.editions.map(edition),
        },
      });
  for (const [id, r] of releases)
    answers[`/v1/catalogue/releases/${id}`] = async (route) => {
      const n = (reads.get(id) || 0) + 1;
      reads.set(id, n);
      if (n > 1 && held.has(id))
        await new Promise<void>((resolve) => waiting.push({ n, resolve }));
      await route
        .fulfill({
          json: {
            id,
            title: r.title,
            artist: artist.name,
            artist_id: "mb-1",
            tracks: [r.song, ...(r.more || [])].map((title, i) =>
              track(r, title, i + 1, n),
            ),
          },
        })
        .catch(() => {});
    };
  await mockApi(page, { answers });
  await page.goto(`/#/library/artist/${artist.id}`);
  await page
    .getByRole("button", { name: `Find more by ${artist.name}` })
    .click();
  return {
    reads: (id: string) => reads.get(id) || 0,
    hold: (id: string) => held.add(id),
    /** Answers the held reads, and waits until the page has had them. */
    release: async () => {
      waiting.splice(0).forEach((w) => w.resolve());
      await page.waitForTimeout(300);
    },
    /** Answers one held read (by its number) and waits for the page. */
    answer: async (n: number) => {
      waiting.find((w) => w.n === n)?.resolve();
      await page.waitForTimeout(300);
    },
  };
}

const UNCONFIRMED =
  "The server was busy, so this may or may not have been saved. Check before trying again.";
const unconfirmed = (route: Route) =>
  route.fulfill({
    status: 503,
    json: {
      code: "busy_unconfirmed",
      detail:
        "The server was busy. Check whether this was saved before trying again.",
    },
  });

test("an unconfirmed album add's late re-read never replaces the album opened since (#262)", async ({
  page,
}) => {
  const a = { id: "r-a", title: "Album A", song: "Song A" };
  const b = { id: "r-b", title: "Album B", song: "Song B" };
  const mock = await catalogue(
    page,
    [
      { id: "g-a", title: "Album A", editions: [a] },
      { id: "g-b", title: "Album B", editions: [b] },
    ],
    unconfirmed,
  );
  await page.getByRole("button", { name: /^Album A/ }).click();
  await expect(page.getByText("Song A")).toBeVisible();
  mock.hold("r-a");
  await page
    .getByRole("button", { name: "Add to library", exact: true })
    .click();
  await expect(page.getByText(UNCONFIRMED)).toBeVisible();
  await expect.poll(() => mock.reads("r-a")).toBe(2);
  // Back to the artist, and on to album B while A's re-read is out.
  await page.locator(".back-link").click();
  await page.getByRole("button", { name: /^Album B/ }).click();
  await expect(page.getByText("Song B")).toBeVisible();
  await mock.release();
  await expect(page.getByRole("heading", { name: "Album B" })).toBeVisible();
  await expect(page.getByText("Song B")).toBeVisible();
  await expect(page.getByText("Song A")).toHaveCount(0);
  expect(page.url()).toContain("r-b");
});

test("an album's songs can't be added until the library lookup says whose they are (#263)", async ({
  page,
}) => {
  const a = { id: "r-a", title: "Album A", song: "Song A" };
  let answer = () => {};
  const answered = new Promise<void>((resolve) => (answer = resolve));
  await catalogue(
    page,
    [{ id: "g-a", title: "Album A", editions: [a] }],
    (route) => route.fulfill({ status: 500 }),
    async (route) => {
      await answered;
      await route.fulfill({ json: { tracks: [null] } }).catch(() => {});
    },
  );
  await page.getByRole("button", { name: /^Album A/ }).click();
  await expect(page.getByText("Song A")).toBeVisible();
  const album = page.getByRole("button", {
    name: "Add to library",
    exact: true,
  });
  const row = page.getByRole("button", { name: "Add Song A to library" });
  await expect(album).toBeDisabled();
  await expect(row).toBeDisabled();
  answer();
  await expect(album).toBeEnabled();
  await expect(row).toBeEnabled();
});

const main = { id: "r-main", title: "Main", song: "Song M" };
const other = { id: "r-other", title: "Other", song: "Song O", explicit: true };
async function onOtherEdition(
  page: Page,
  add: (route: Route) => Promise<void>,
) {
  const mock = await catalogue(
    page,
    [{ id: "g-x", title: "Main", editions: [main, other] }],
    add,
  );
  await page.getByRole("button", { name: /^Main/ }).click();
  await expect(page.getByText("Song M")).toBeVisible();
  await page.getByRole("button", { name: /Choose another edition/ }).click();
  await page.getByRole("button", { name: /Other/ }).click();
  await expect(page.getByText("Song O")).toBeVisible();
  return mock;
}
const added = (created: number, existing: number) => (route: Route) =>
  route.fulfill({
    json: { created, existing, skipped: 0, downloads_enabled: true },
  });

test("a new request for an explicit edition reads the edition again (#263)", async ({
  page,
}) => {
  const mock = await onOtherEdition(page, added(1, 0));
  expect(mock.reads("r-other")).toBe(1);
  await page.getByRole("button", { name: "Add Song O to library" }).click();
  await expect.poll(() => mock.reads("r-other")).toBe(2);
});

test("a row Add's late re-read never replaces the edition chosen since (#263)", async ({
  page,
}) => {
  const mock = await onOtherEdition(page, added(0, 1));
  mock.hold("r-other");
  await page.getByRole("button", { name: "Add Song O to library" }).click();
  await expect.poll(() => mock.reads("r-other")).toBe(2);
  await page.getByRole("button", { name: /Choose another edition/ }).click();
  await page.getByRole("button", { name: /^Main/ }).click();
  await expect(page.getByText("Song M")).toBeVisible();
  await mock.release();
  await expect(page.getByText("Song M")).toBeVisible();
  await expect(page.getByText("Song O")).toHaveCount(0);
  expect(page.url()).toContain("r-main");
});

test("adding a song you paused shows it paused and points to Downloads (#264)", async ({
  page,
}) => {
  const a = { id: "r-a", title: "Album A", song: "Song A" };
  await catalogue(
    page,
    [{ id: "g-a", title: "Album A", editions: [a] }],
    (route) =>
      route.fulfill({
        json: {
          created: 0,
          existing: 0,
          skipped: 0,
          paused: 1,
          downloads_enabled: true,
        },
      }),
  );
  await page.getByRole("button", { name: /^Album A/ }).click();
  await page.getByRole("button", { name: "Add Song A to library" }).click();
  await expect(page.locator(".notice-area")).toContainText(
    "Paused. Resume it in Downloads.",
  );
  await expect(page.getByRole("img", { name: "Paused" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Add Song A to library" }),
  ).toHaveCount(0);
});

test("a library lookup that fails says so, and Try again asks again (#263)", async ({
  page,
}) => {
  const a = { id: "r-a", title: "Album A", song: "Song A" };
  let lookups = 0;
  await catalogue(
    page,
    [{ id: "g-a", title: "Album A", editions: [a] }],
    (route) => route.fulfill({ status: 500 }),
    (route) =>
      ++lookups <= 2
        ? route.fulfill({ status: 500, json: { detail: "Broken" } })
        : route.fulfill({ json: { tracks: [null] } }),
  );
  await page.getByRole("button", { name: /^Album A/ }).click();
  const notices = page.locator(".notice-area");
  await expect(notices).toContainText(
    "Couldn't check your library. Try again.",
  );
  const add = page.getByRole("button", { name: "Add to library", exact: true });
  await expect(add).toBeDisabled();
  await notices.getByRole("button", { name: "Try again" }).click();
  await expect(add).toBeEnabled();
  expect(lookups).toBe(3);
});

test("an older row refresh never overwrites a newer one on the same edition (#263)", async ({
  page,
}) => {
  const two = {
    ...other,
    more: ["Song P"],
    // Read 2 follows the first Add, read 3 the second.
    requested: (read: number) =>
      read === 2 ? ["Song O"] : read >= 3 ? ["Song O", "Song P"] : [],
  };
  const mock = await catalogue(
    page,
    [{ id: "g-x", title: "Main", editions: [main, two] }],
    added(1, 0),
  );
  await page.getByRole("button", { name: /^Main/ }).click();
  await page.getByRole("button", { name: /Choose another edition/ }).click();
  await page.getByRole("button", { name: /Other/ }).click();
  await expect(page.getByText("Song P")).toBeVisible();
  mock.hold("r-other");
  await page.getByRole("button", { name: "Add Song O to library" }).click();
  await expect.poll(() => mock.reads("r-other")).toBe(2);
  await page.getByRole("button", { name: "Add Song P to library" }).click();
  await expect.poll(() => mock.reads("r-other")).toBe(3);
  // The newer answer first, then the older one.
  await mock.answer(3);
  await mock.answer(2);
  await expect(
    page.getByRole("button", { name: "Add Song P to library" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Add Song O to library" }),
  ).toHaveCount(0);
});

test("an older poll never overwrites a newer row refresh (#263)", async ({
  page,
}) => {
  const two = {
    ...other,
    more: ["Song P"],
    // Read 2 is the poll (Song O on its way), read 3 the row Add's refresh:
    // as many songs on their way, so the poll carries on.
    requested: (read: number) => (read <= 2 ? ["Song O"] : ["Song P"]),
  };
  const mock = await catalogue(
    page,
    [{ id: "g-x", title: "Main", editions: [main, two] }],
    added(1, 0),
  );
  await page.getByRole("button", { name: /^Main/ }).click();
  await page.getByRole("button", { name: /Choose another edition/ }).click();
  await page.getByRole("button", { name: /Other/ }).click();
  await expect(page.getByText("Song P")).toBeVisible();
  mock.hold("r-other");
  await expect.poll(() => mock.reads("r-other"), { timeout: 8000 }).toBe(2);
  await page.getByRole("button", { name: "Add Song P to library" }).click();
  await expect.poll(() => mock.reads("r-other")).toBe(3);
  await mock.answer(3);
  await mock.answer(2);
  await expect(
    page.getByRole("button", { name: "Add Song P to library" }),
  ).toHaveCount(0);
});

test("a row Add answered after an edition switch doesn't drop the new edition's refresh (#263)", async ({
  page,
}) => {
  let answerOther = () => {};
  const otherAnswered = new Promise<void>((resolve) => (answerOther = resolve));
  const mine = {
    ...main,
    requested: (read: number) => (read >= 3 ? ["Song M"] : []),
  };
  const mock = await catalogue(
    page,
    [{ id: "g-x", title: "Main", editions: [mine, other] }],
    async (route) => {
      if (route.request().postData()?.includes("r-other")) await otherAnswered;
      await route
        .fulfill({
          json: {
            created: 1,
            existing: 0,
            skipped: 0,
            downloads_enabled: true,
          },
        })
        .catch(() => {});
    },
  );
  await page.getByRole("button", { name: /^Main/ }).click();
  await page.getByRole("button", { name: /Choose another edition/ }).click();
  await page.getByRole("button", { name: /Other/ }).click();
  await page.getByRole("button", { name: "Add Song O to library" }).click();
  // Back to the main edition while that Add is out.
  await page.getByRole("button", { name: /Choose another edition/ }).click();
  await page.getByRole("button", { name: /^Main/ }).click();
  await expect(page.getByText("Song M")).toBeVisible();
  mock.hold("r-main");
  await page
    .getByRole("button", { name: "Add to library", exact: true })
    .click();
  await expect.poll(() => mock.reads("r-main")).toBe(3);
  // The other edition's Add answers now, and its row asks for a refresh.
  answerOther();
  await page.waitForTimeout(300);
  await mock.answer(3);
  await expect(
    page.getByRole("button", { name: "Add Song M to library" }),
  ).toHaveCount(0);
});

test("an older poll never undoes the edition chosen again (#263)", async ({
  page,
}) => {
  const polled = {
    ...main,
    more: ["Song P"],
    // Song M on its way (so the album polls), then in your library.
    requested: (read: number) => (read <= 2 ? ["Song M"] : []),
    saved: (read: number) => (read >= 3 ? ["Song M"] : []),
  };
  const mock = await catalogue(
    page,
    [{ id: "g-x", title: "Main", editions: [polled, other] }],
    added(1, 0),
  );
  await page.getByRole("button", { name: /^Main/ }).click();
  await expect(page.getByText("Song M")).toBeVisible();
  mock.hold("r-main");
  await expect.poll(() => mock.reads("r-main"), { timeout: 8000 }).toBe(2);
  // An Add meanwhile, so the next read of the album is a read of its own.
  await page.getByRole("button", { name: "Add Song P to library" }).click();
  await expect(page.getByRole("img", { name: "Adding" })).toHaveCount(2);
  // The same edition chosen again while the poll is out.
  await page.getByRole("button", { name: /Choose another edition/ }).click();
  await page.getByRole("button", { name: /^Main/ }).click();
  await expect.poll(() => mock.reads("r-main")).toBe(3);
  await mock.answer(3);
  const saved = page.getByRole("img", { name: "In your library" });
  await expect(saved).toBeVisible();
  await mock.answer(2);
  await expect(saved).toBeVisible();
});
