import { expect, test } from "@playwright/test";
import { album, mockApi } from "./mock";

const item = (id: string, name: string) => ({
  id,
  name,
  artist: "Someone",
  artist_id: "a-" + id,
});
const feed = (title: string) => ({
  state: "ready",
  title,
  artists: [item("ar1", "Artist one")],
  albums: [item("al1", "Album one")],
  songs: [
    {
      ...item("s1", "Song one"),
      title: "Song one",
      album: "Album one",
      duration: 200,
    },
  ],
});

// Home: what you added lately, then recommendations above popularity.
// Recommendations show artists and albums only, never songs.
test("Home order, with no song recommendations", async ({ page }) => {
  await mockApi(page, {
    answers: {
      "/v1/home/recent": (route) =>
        route.fulfill({
          json: {
            albums: [
              { id: album.id, title: album.title, artist: album.artist },
            ],
          },
        }),
      "/v1/home/recommended": (route) => route.fulfill({ json: feed("") }),
      "/v1/home/popular": (route) =>
        route.fulfill({ json: feed("Popular in Australia") }),
    },
  });
  await page.goto("/#/home");
  const sections = page.locator(
    ".home-page > .home-section > .section-title h2",
  );
  await expect(sections).toHaveText([
    "Your mixes",
    "Recently added",
    "Recommended for you",
    "Popular in Australia",
  ]);
  const recommended = page
    .locator(".home-section")
    .filter({ hasText: "Recommended for you" });
  await expect(recommended.locator("h3")).toHaveText(["Artists", "Albums"]);
  await expect(recommended).not.toContainText("Song one");
  const popular = page
    .locator(".home-section")
    .filter({ hasText: "Popular in Australia" });
  await expect(popular.locator("h3")).toHaveText([
    "Artists",
    "Albums",
    "Songs",
  ]);
});

for (const appearance of ["light", "dark"]) {
  test(`Mixes stay in one row and play prepared audio (${appearance})`, async ({
    page,
  }) => {
    const kinds = [
      "daily",
      "fresh",
      "releases",
      "artist",
      "genre",
      "decade",
      "popular",
      "recent",
      "deep",
      "back",
    ];
    const mixes = kinds.map((kind, i) => ({
      id: "edition-" + i,
      kind,
      title:
        i === 0 ? "Daily blend" : kind === "decade" ? "Your 1990s" : "Mix " + i,
      day: "2026-10-16",
      reason: "Music close to your taste",
      tracks: [
        {
          ...album.tracks[0],
          id: `mix:edition-${i}:0`,
          server_state: "not_in_library",
        },
      ],
    }));
    const mock = await mockApi(page, {
      appearance,
      answers: {
        "/v1/home/mixes": (route) =>
          route.fulfill({ json: { mixes, preparing: false } }),
        "/v1/playback/mix%3Aedition-0%3A0/url": (route) =>
          route.fulfill({
            json: { url: "/api/v1/playback/files/prepared", expires_in: 21600 },
          }),
        "/playback/files/prepared": (route) =>
          route.fulfill({
            status: 200,
            contentType: "audio/mpeg",
            body: Buffer.alloc(64),
          }),
        "/v1/mixes/edition-0/save": (route) =>
          route.fulfill({ json: { id: "saved-mix" } }),
        "/v1/mixes/edition-1/hide": (route) =>
          route.fulfill({ json: { hidden: true } }),
      },
    });
    await page.goto("/#/home");
    const cards = page.locator(".mix-card");
    await expect(cards).toHaveCount(10);
    const positions = await cards.evaluateAll((nodes) =>
      nodes.map((node) => node.getBoundingClientRect().top),
    );
    expect(Math.max(...positions) - Math.min(...positions)).toBeLessThan(2);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    const covers = await cards
      .locator("svg")
      .evaluateAll((nodes) => nodes.map((node) => node.innerHTML));
    expect(new Set(covers).size).toBe(10);
    await expect(
      cards.filter({ hasText: "Your 1990s" }).locator("svg text"),
    ).toHaveText("90s");
    // #56: a click plays the mix at once; its songs are one option away.
    const playback = page.waitForRequest(
      (request) =>
        request.url().includes("/v1/playback/") && request.method() === "POST",
    );
    await page.getByRole("button", { name: "Play Daily blend" }).click();
    expect(decodeURIComponent((await playback).url())).toContain(
      "mix:edition-0:0/url",
    );
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(
      mock.unknown.filter((path) => path.includes("remote-playback")),
    ).toEqual([]);
    await page.getByRole("button", { name: "Options for Daily blend" }).click();
    const menu = page.getByRole("menu", { name: "Daily blend" });
    await expect(menu.getByRole("menuitem")).toHaveText([
      "Show songs",
      "Shuffle",
      "Save this edition",
      "Hide this edition",
    ]);
    // The menu takes focus and the arrow keys move through it.
    await expect(
      menu.getByRole("menuitem", { name: "Show songs" }),
    ).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("menuitem", { name: "Shuffle" })).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: "Daily blend" });
    await expect(dialog).toBeVisible();
    // #154: the mix dialog keeps its own width, not the generic 32rem cap.
    const box = await dialog.boundingBox();
    expect(box!.width).toBeGreaterThan(
      Math.min(700, page.viewportSize()!.width - 40),
    );
    await dialog.getByRole("button", { name: "Save this edition" }).click();
    await expect(dialog.getByRole("status")).toContainText("Playlist saved");
    await dialog.getByRole("button", { name: "Close mix" }).click();
    await expect(dialog).not.toBeVisible();
    // A right-click opens the same options: save without opening the songs.
    await page
      .getByRole("button", { name: "Play Daily blend" })
      .click({ button: "right" });
    await menu.getByRole("menuitem", { name: "Save this edition" }).click();
    await expect(
      page.getByRole("region", { name: "Your mixes" }).getByRole("status"),
    ).toContainText("Playlist saved");
    await page.getByRole("button", { name: "Options for Mix 1" }).click();
    await page
      .getByRole("menu", { name: "Mix 1" })
      .getByRole("menuitem", { name: "Hide this edition" })
      .click();
    await expect(cards).toHaveCount(9);
  });
}

// #56: on a touchscreen, holding a mix opens its options instead of playing it.
test("A long press on a mix opens its options; Shuffle plays it", async ({
  page,
}) => {
  const tracks = [0, 1, 2].map((i) => ({
    ...album.tracks[0],
    id: `mix:edition-0:${i}`,
    title: "Mix song " + i,
    server_state: "not_in_library",
  }));
  const played: string[] = [];
  await mockApi(page, {
    answers: {
      "/v1/home/mixes": (route) =>
        route.fulfill({
          json: {
            mixes: [
              {
                id: "edition-0",
                kind: "daily",
                title: "Daily blend",
                day: "2026-10-16",
                reason: "Music close to your taste",
                tracks,
              },
            ],
            preparing: false,
          },
        }),
    },
  });
  await page.route("**/api/v1/playback/*/url", (route) => {
    played.push(decodeURIComponent(route.request().url()));
    return route.fulfill({
      json: { url: "/api/v1/playback/files/prepared", expires_in: 21600 },
    });
  });
  await page.goto("/#/home");
  const card = page.getByRole("button", { name: "Play Daily blend" });
  await card.dispatchEvent("pointerdown", { pointerType: "touch" });
  const menu = page.getByRole("menu", { name: "Daily blend" });
  await expect(menu).toBeVisible();
  // The finger lifting is not a tap: nothing plays.
  await card.dispatchEvent("pointerup", { pointerType: "touch" });
  await card.dispatchEvent("click");
  await page.waitForTimeout(300);
  expect(played).toEqual([]);
  await menu.getByRole("menuitem", { name: "Shuffle" }).click();
  await expect.poll(() => played.length).toBeGreaterThan(0);
  expect(played[0]).toMatch(/mix:edition-0:\d\/url/);
});

test("Radio starts from Home, refills before it runs out and reports skips", async ({
  page,
}) => {
  const entry = (index: number) => ({
    ...album.tracks[0],
    id: `mix:radio-1:${index}`,
    title: "Radio song " + index,
    server_state: "not_in_library",
  });
  const pages: string[] = [];
  const skips: unknown[] = [];
  await mockApi(page, {
    answers: {
      "/v1/home/mixes": (route) =>
        route.fulfill({
          json: {
            mixes: [],
            preparing: false,
            radio: { title: "Mark's radio", ready: 20 },
          },
        }),
      "/v1/radio": (route) => {
        const after = new URL(route.request().url()).searchParams.get("after");
        pages.push(after ?? "");
        const start = after ? Number(after.split(":")[2]) + 1 : 0;
        return route.fulfill({
          json: {
            id: "radio-1",
            title: "Mark's radio",
            preparing: false,
            tracks: start < 4 ? [entry(start), entry(start + 1)] : [],
          },
        });
      },
      "/v1/radio/feedback": (route) => {
        skips.push(route.request().postDataJSON());
        return route.fulfill({ json: { recorded: true } });
      },
    },
  });
  await page.route("**/api/v1/playback/*/url", (route) =>
    route.fulfill({
      json: { url: "/api/v1/playback/files/prepared", expires_in: 21600 },
    }),
  );
  await page.route("**/api/v1/playback/files/prepared", (route) =>
    route.fulfill({
      status: 200,
      contentType: "audio/mpeg",
      body: Buffer.alloc(64),
    }),
  );
  await page.goto("/#/home");
  await page.locator(".mix-card", { hasText: "Mark's radio" }).click();
  await expect(page.getByRole("region", { name: "Now playing" })).toContainText(
    "Radio song 0",
  );
  // Two songs left is below the refill threshold, so the next page is asked for.
  await expect.poll(() => pages).toEqual(["", "mix:radio-1:1"]);
  await page.getByRole("button", { name: "Next song" }).first().click();
  await expect(page.getByRole("region", { name: "Now playing" })).toContainText(
    "Radio song 1",
  );
  await expect
    .poll(() => skips)
    .toEqual([{ track_id: "mix:radio-1:0", action: "skip" }]);
});

// #55: the chart holds up to 100 songs; Home lists ten, then all on request.
test("Popular songs show ten, then all on request", async ({ page }) => {
  const songs = Array.from({ length: 12 }, (_, i) => ({
    ...item("s" + i, "Song " + i),
    title: "Song " + i,
    album: "Album one",
    duration: 200,
  }));
  await mockApi(page, {
    answers: {
      "/v1/home/recommended": (route) => route.fulfill({ json: feed("") }),
      "/v1/home/popular": (route) =>
        route.fulfill({ json: { ...feed("Popular in Australia"), songs } }),
    },
  });
  await page.goto("/#/home");
  const list = page.locator(".home-songs");
  await expect(list.locator(".song-row")).toHaveCount(10);
  await list.getByRole("button", { name: "Show all 12 songs" }).click();
  await expect(list.locator(".song-row")).toHaveCount(12);
});

// #182: the shelf's edges fade only while there is more to scroll that way.
test("the song shelf fades the edge it can still scroll towards", async ({
  page,
}) => {
  const songs = Array.from({ length: 10 }, (_, i) => ({
    ...item("s" + i, "Song " + i),
    title: "Song " + i,
    album: "Album one",
    duration: 200,
  }));
  await mockApi(page, {
    answers: {
      "/v1/home/recommended": (route) => route.fulfill({ json: feed("") }),
      "/v1/home/popular": (route) =>
        route.fulfill({ json: { ...feed("Popular in Australia"), songs } }),
    },
  });
  await page.goto("/#/home");
  const list = page.locator(".home-songs");
  await expect(list.locator(".song-row")).toHaveCount(10);
  const edges = () =>
    list.evaluate((el) => {
      const s = getComputedStyle(el);
      return [
        s.getPropertyValue("--shelf-fade-start"),
        s.getPropertyValue("--shelf-fade-end"),
      ].join(" ");
    });
  expect(await list.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(
    true,
  );
  await expect.poll(edges).toBe("0px 32px");
  await list.evaluate((el) => (el.scrollLeft = el.scrollWidth));
  await expect.poll(edges).toBe("32px 0px");
});

test("#182 (Codex round 2): a shelf that fits shows no fade", async ({
  page,
}) => {
  const songs = Array.from({ length: 3 }, (_, i) => ({
    ...item("s" + i, "Song " + i),
    title: "Song " + i,
    album: "Album one",
    duration: 200,
  }));
  await mockApi(page, {
    answers: {
      "/v1/home/recommended": (route) => route.fulfill({ json: feed("") }),
      "/v1/home/popular": (route) =>
        route.fulfill({ json: { ...feed("Popular in Australia"), songs } }),
    },
  });
  await page.goto("/#/home");
  const list = page.locator(".home-songs");
  await expect(list.locator(".song-row")).toHaveCount(3);
  expect(await list.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(
    false,
  );
  const edges = await list.evaluate((el) => {
    const s = getComputedStyle(el);
    return `${s.getPropertyValue("--shelf-fade-start")} ${s.getPropertyValue("--shelf-fade-end")}`;
  });
  expect(edges).toBe("0px 0px");
});

// #105: a first-ever chart build can take several minutes; Home keeps
// checking while it is preparing and shows the chart when it arrives.
test("a chart that takes over two minutes still appears on an open Home", async ({
  page,
}) => {
  let reads = 0;
  await page.clock.install();
  await mockApi(page, {
    answers: {
      "/v1/home/popular": (route) =>
        ++reads <= 8
          ? route.fulfill({
              json: {
                state: "preparing",
                title: "",
                source: "",
                artists: [],
                albums: [],
                songs: [],
              },
            })
          : route.fulfill({ json: feed("Popular in Australia") }),
    },
  });
  await page.goto("/#/home");
  await expect(
    page.getByText("Finding what\u2019s popular\u2026"),
  ).toBeVisible();
  // Each check waits for its answer before the next is timed, so the clock
  // moves a minute at a time until the ninth check has been made.
  await expect
    .poll(
      async () => {
        await page.clock.runFor(60_000);
        return reads;
      },
      { intervals: [100], timeout: 20_000 },
    )
    .toBeGreaterThan(8);
  const popular = page
    .locator(".home-section")
    .filter({ hasText: "Popular in Australia" });
  await expect(popular.getByText("Song one")).toBeVisible();
});
