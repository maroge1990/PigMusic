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
  const sections = page.locator(".home-page > .home-section > h2");
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
    await cards.first().click();
    const dialog = page.getByRole("dialog", { name: "Daily blend" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Save this edition" }).click();
    await expect(dialog.getByRole("status")).toContainText("Playlist saved");
    const playback = page.waitForRequest(
      (request) =>
        request.url().includes("/v1/playback/") && request.method() === "POST",
    );
    await dialog.getByRole("button", { name: "Play mix" }).click();
    expect(decodeURIComponent((await playback).url())).toContain(
      "mix:edition-0:0/url",
    );
    await expect(dialog).not.toBeVisible();
    expect(
      mock.unknown.filter((path) => path.includes("remote-playback")),
    ).toEqual([]);
    await cards.nth(1).click();
    const hidden = page.getByRole("dialog", { name: "Mix 1" });
    await hidden.getByText("Mix options", { exact: true }).click();
    await hidden.getByRole("button", { name: "Hide this edition" }).click();
    await expect(cards).toHaveCount(9);
    await expect(hidden).not.toBeVisible();
  });
}

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
