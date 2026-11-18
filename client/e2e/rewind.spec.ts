import { expect, test, type Page } from "@playwright/test";
import { album, mockApi } from "./mock";

// Ten top songs "Song 1" … "Song 10"; Song 7 can no longer play.
const songs = Array.from({ length: 10 }, (_, i) => ({
  track: {
    ...album.tracks[0],
    id: "t" + (i + 1),
    title: "Song " + (i + 1),
    cover_url: album.cover_url,
    ...(i === 6 ? { available: false } : {}),
  },
  plays: 20 - i,
}));

// The week so far: Monday to Wednesday, Tuesday the busiest.
const day = (n: number, minutes: number) => ({
  start: 1762700000 + n * 86400,
  minutes,
  plays: minutes ? 3 : 0,
});

const report = (period: string) => {
  const empty = period === "month";
  return {
    period,
    start: 1762700000,
    end: 1763200000,
    timezone: "Australia/Melbourne",
    plays: empty ? 0 : 45,
    minutes: empty ? 0 : period === "year" ? 5678 : 1234,
    top_songs: empty ? [] : songs,
    top_artists: empty
      ? []
      : [
          {
            name: "Charli xcx",
            plays: 21,
            minutes: 63,
            id: "artist-1",
            musicbrainz_artist_id: "mb-charli",
            new: false,
            cover_url: album.cover_url,
          },
          {
            name: "Caroline Polachek",
            plays: 9,
            minutes: 30,
            id: null,
            musicbrainz_artist_id: "mb-caroline",
            new: true,
            cover_url: null,
          },
          {
            name: "Nobody Known",
            plays: 2,
            minutes: 6,
            id: null,
            musicbrainz_artist_id: null,
            new: false,
            cover_url: null,
          },
        ],
    top_albums: empty
      ? []
      : [
          {
            title: "Brat",
            artist: "Charli xcx",
            plays: 21,
            cover_url: album.cover_url,
            id: "album-1",
            musicbrainz_release_id: "rel-brat",
          },
          {
            title: "Desire, I Want to Turn Into You",
            artist: "Caroline Polachek",
            plays: 9,
            cover_url: null,
            id: null,
            musicbrainz_release_id: "rel-desire",
          },
          {
            title: "Unknown Album",
            artist: "Nobody Known",
            plays: 2,
            cover_url: null,
            id: null,
            musicbrainz_release_id: "rel-unknown",
          },
          {
            title: "Lost Album",
            artist: "Nobody Known",
            plays: 1,
            cover_url: null,
            id: null,
            musicbrainz_release_id: null,
          },
        ],
    new_artists: empty ? [] : ["Caroline Polachek"],
    top_mixes: empty
      ? []
      : [
          { title: "Radio", plays: 6, id: "m1", kind: "radio" },
          { title: "Saved mix", plays: 4, id: "m2", kind: "saved:abc" },
        ],
    days: empty ? [] : [day(0, 40), day(1, 80), day(2, 20)],
  };
};

async function setUp(page: Page) {
  const periods: string[] = [];
  await mockApi(page, {
    answers: {
      "/v1/rewind": (route) => {
        const period =
          new URL(route.request().url()).searchParams.get("period") || "";
        periods.push(period);
        return route.fulfill({ json: report(period) });
      },
      "/v1/catalogue/releases/rel-desire": (route) =>
        route.fulfill({
          json: {
            id: "rel-desire",
            title: "Desire, I Want to Turn Into You",
            artist: "Caroline Polachek",
            artist_id: "mb-caroline",
            tracks: [],
          },
        }),
      // A release credited to no known artist (empty artist_id).
      "/v1/catalogue/releases/rel-unknown": (route) =>
        route.fulfill({
          json: {
            id: "rel-unknown",
            title: "Unknown Album",
            artist: "Nobody Known",
            artist_id: "",
            tracks: [],
          },
        }),
    },
  });
  await page.route("**/api/v1/playback/*/url", (route) =>
    route.fulfill({
      json: { url: "/api/v1/playback/files/silence", expires_in: 60 },
    }),
  );
  await page.route("**/api/v1/playback/files/silence", (route) =>
    route.fulfill({ contentType: "audio/wav", body: Buffer.alloc(64) }),
  );
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
  return periods;
}

// #216: the Home card shows this week and opens Rewind.
test("the Home card shows the week's minutes, plays and days", async ({
  page,
}) => {
  await setUp(page);
  await page.goto("/#/home");
  const card = page.locator("a.rewind-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Rewind");
  await expect(card).toContainText("This week");
  await expect(card.locator(".rewind-figure strong")).toHaveText("1,234");
  await expect(card).toContainText("minutes · 45 plays");
  await expect(card.locator(".rewind-card-covers .cover")).toHaveCount(3);
  // Seven bars, Monday first; days still to come are empty.
  const bars = card.locator(".rewind-bar");
  await expect(bars).toHaveCount(7);
  await expect(card.locator(".rewind-bar.future")).toHaveCount(4);
  await expect(card.locator(".rewind-bar-day")).toHaveText([
    "M",
    "T",
    "W",
    "T",
    "F",
    "S",
    "S",
  ]);
  // Heights are relative to the busiest day (Tuesday, 80 minutes).
  const heights = await card
    .locator(".rewind-bar-fill")
    .evaluateAll((els) => els.map((el) => (el as HTMLElement).style.height));
  expect(heights).toEqual(["50%", "100%", "25%"]);
  // The old Mixes button is gone.
  await expect(page.getByRole("link", { name: "Your Rewind" })).toHaveCount(0);

  await card.click();
  await expect(page).toHaveURL(/#\/home\/rewind$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Rewind");
});

test("the Home card is hidden while the week has no plays", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/rewind": (route) => route.fulfill({ json: report("month") }),
    },
  });
  await page.goto("/#/home");
  await expect(page.getByRole("heading", { name: "Your mixes" })).toBeVisible();
  await expect(page.locator(".rewind-card")).toHaveCount(0);
});

// #216: one list at a time; the chip stays across periods.
test("Rewind switches period and list", async ({ page }) => {
  const periods = await setUp(page);
  await page.goto("/#/home/rewind");
  await expect(page.locator(".rewind-hero .rewind-figure strong")).toHaveText(
    "1,234",
  );
  await expect(page.locator(".rewind-hero")).toContainText(
    "minutes · 45 plays",
  );
  // The top mixes use the mix cover palette; an unknown kind takes daily.
  const fills = await page
    .locator(".rewind-mixes svg > rect")
    .evaluateAll((els) => els.map((el) => el.getAttribute("fill")));
  expect(fills).toEqual([
    "var(--mix-radio-background)",
    "var(--mix-daily-background)",
  ]);
  // The old sections are gone.
  await expect(page.getByText("New to you")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Play your top songs" }),
  ).toHaveCount(0);

  const list = page.locator(".rewind-ranked > li");
  await expect(list).toHaveCount(10);
  await expect(list.first()).toContainText("Song 1");
  // #248: the one song row, with its rank and plays.
  await expect(list.first()).toContainText("Charli xcx · Brat · 20 plays");
  await expect(list.first().locator(".song-rank.top")).toHaveText("1");
  await expect(list.nth(3).locator(".song-rank.top")).toHaveCount(0);

  await page.getByRole("button", { name: "Artists" }).click();
  await expect(page.getByRole("button", { name: "Artists" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(list).toHaveCount(3);
  await expect(list.first()).toContainText("63 min");
  await expect(list.first().locator(".artist-cover")).toHaveCount(1);

  await page.getByRole("link", { name: "Month" }).click();
  await expect.poll(() => periods).toEqual(["week", "month"]);
  await expect(
    page.getByRole("heading", { name: "Nothing played yet" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Month" })).toHaveAttribute(
    "aria-current",
    "page",
  );

  await page.getByRole("link", { name: "Year" }).click();
  await expect(page.locator(".rewind-hero .rewind-figure strong")).toHaveText(
    "5,678",
  );
  // Still on Artists.
  await expect(page.getByRole("button", { name: "Artists" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(list.first()).toContainText("Charli xcx");
});

// #216: song 5 plays 5, 6, 8, 9, 10; the song that can't play is skipped.
test("tapping a song plays it and the songs ranked after it", async ({
  page,
}) => {
  await setUp(page);
  await page.goto("/#/home/rewind");
  const rows = page.locator(".rewind-ranked > li");
  await expect(rows.nth(6).locator(".song-main")).toBeDisabled();
  await rows.nth(4).locator(".song-main").click();
  await expect(page.locator(".mini-player")).toContainText("Song 5");
  await expect
    .poll(async () =>
      page.evaluate(() =>
        JSON.parse(
          localStorage.getItem("pig-music-queue") || "null",
        )?.songs.map((s: { title: string }) => s.title),
      ),
    )
    .toEqual(["Song 5", "Song 6", "Song 8", "Song 9", "Song 10"]);
});

// #248: a Rewind song has the one song menu, from ⋯ or a right-click.
test("a Rewind song row has the song menu", async ({ page }) => {
  await setUp(page);
  await page.goto("/#/home/rewind");
  const row = page.locator(".rewind-ranked > li").first();
  await row.getByRole("button", { name: "More options for Song 1" }).click();
  const menu = page.getByRole("menu", { name: "Song 1" });
  await expect(menu.getByRole("menuitem").first()).toHaveText("Play next");
  await expect(menu.getByRole("menuitem").first()).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await row.locator(".song-text").click({ button: "right" });
  await expect(menu).toBeVisible();
});

// #216: library page first, then the catalogue, else not a link.
test("artist and album rows open the right pages", async ({ page }) => {
  await setUp(page);
  await page.goto("/#/home/rewind");
  await page.getByRole("button", { name: "Artists" }).click();
  const rows = page.locator(".rewind-ranked > li");
  await expect(rows.nth(0).locator("a")).toHaveAttribute(
    "href",
    "#/library/artist/artist-1",
  );
  await expect(rows.nth(1).locator("a")).toHaveAttribute(
    "href",
    "#/search/artist/mb-caroline?name=Caroline+Polachek",
  );
  await expect(rows.nth(1).locator(".rewind-new")).toHaveText("New");
  await expect(page.locator(".rewind-new")).toHaveCount(1);
  await expect(rows.nth(2).locator("a, button")).toHaveCount(0);

  await page.getByRole("button", { name: "Albums" }).click();
  await expect(rows.nth(0).locator("a")).toHaveAttribute(
    "href",
    "#/library/album/album-1",
  );
  await expect(rows.nth(3).locator("a, button")).toHaveCount(0);
  await rows.nth(1).locator("button").click();
  await expect(page).toHaveURL(
    /#\/search\/artist\/mb-caroline\?name=Caroline\+Polachek&release=rel-desire$/,
  );
});

// #216 (Codex round 1): a release with no artist identity still opens.
test("a catalogue album with no known artist opens Search", async ({
  page,
}) => {
  await setUp(page);
  await page.goto("/#/home/rewind");
  await page.getByRole("button", { name: "Albums" }).click();
  await page.locator(".rewind-ranked > li").nth(2).getByRole("button").click();
  await expect(page).toHaveURL(
    /#\/search\?q=Unknown%20Album%20Nobody%20Known$/,
  );
  await expect(page.getByText("can't be opened")).toHaveCount(0);
});
