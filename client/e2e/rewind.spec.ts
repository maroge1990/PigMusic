import { expect, test } from "@playwright/test";
import { album, mockApi } from "./mock";

const report = (period: string) => ({
  period,
  start: 1762700000,
  end: 1763200000,
  timezone: "Australia/Melbourne",
  plays: period === "week" ? 45 : 0,
  minutes: period === "week" ? 1234 : 0,
  top_songs:
    period === "week"
      ? [
          { track: album.tracks[1], plays: 9 },
          { track: { ...album.tracks[0], available: false }, plays: 7 },
          { track: album.tracks[0], plays: 5 },
        ]
      : [],
  top_artists:
    period === "week" ? [{ name: "Charli xcx", plays: 21, minutes: 63 }] : [],
  top_albums:
    period === "week"
      ? [
          {
            title: "Brat",
            artist: "Charli xcx",
            plays: 21,
            cover_url: album.cover_url,
          },
        ]
      : [],
  new_artists: period === "week" ? ["Caroline Polachek"] : [],
  top_mixes: period === "week" ? [{ title: "Daily Mix 1", plays: 4 }] : [],
});

// #203: Rewind shows a person's listening and plays their top songs.
test("Rewind shows the week, plays the top songs and switches period", async ({
  page,
}) => {
  const periods: string[] = [];
  await mockApi(page, {
    answers: {
      "/v1/rewind": (route) => {
        const period =
          new URL(route.request().url()).searchParams.get("period") || "";
        periods.push(period);
        return route.fulfill({ json: report(period) });
      },
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
  await page.goto("/#/home");
  await page.getByRole("link", { name: "Your Rewind" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Your Rewind",
  );
  const totals = page.locator(".rewind-totals");
  await expect(totals).toContainText("Minutes listened1,234");
  await expect(totals).toContainText("Listens45");
  await expect(page.getByText("Caroline Polachek")).toBeVisible();
  await expect(page.getByText("Daily Mix 1")).toBeVisible();
  await expect(page.getByText("21 plays · 63 minutes")).toBeVisible();
  await expect(page.getByText("No longer available")).toBeVisible();
  expect(periods).toEqual(["week"]);

  await page.getByRole("button", { name: "Play your top songs" }).click();
  await expect(page.locator(".mini-player")).toContainText("360");
  // The song that can no longer play is left out of the queue.
  await expect
    .poll(async () =>
      page.evaluate(() =>
        JSON.parse(
          localStorage.getItem("pig-music-queue") || "null",
        )?.songs.map((s: { title: string }) => s.title),
      ),
    )
    .toEqual(["360", "Von dutch"]);

  await page.getByRole("link", { name: "Month" }).click();
  await expect.poll(() => periods).toEqual(["week", "month"]);
  await expect(page.getByText("Nothing played yet this month.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Month" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});
