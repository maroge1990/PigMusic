import { expect, test, type Route } from "@playwright/test";
import { album, mockApi } from "./mock";

const photo = `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><rect width="512" height="512" fill="#888"/></svg>`;

test("#175: a missing cover looks again three times, then only when the library changes", async ({
  page,
}) => {
  await page.clock.install();
  let exists = false;
  const asked: string[] = [];
  const cover = (route: Route) => {
    asked.push(new URL(route.request().url()).searchParams.get("r") ?? "");
    return exists
      ? route.fulfill({ contentType: "image/svg+xml", body: photo })
      : route.fulfill({ status: 404, json: { detail: "Not found" } });
  };
  await mockApi(page, {
    answers: {
      "/v1/albums/album-2": (route) =>
        route.fulfill({
          json: {
            ...album,
            id: "album-2",
            title: "Sucker",
            cover_url: "/api/v1/artwork/late",
          },
        }),
      "/v1/artwork/late": cover,
    },
  });
  await page.goto("/#/library/album/album-2");
  const frame = page.locator("main .cover").first();
  await expect(frame).toContainText("S");
  await expect.poll(() => asked).toEqual(["0"]);

  // The server fetches a missing cover in the background: three early looks.
  await page.clock.runFor(2_000);
  await expect.poll(() => asked).toEqual(["0", "1"]);
  await page.clock.runFor(6_000);
  await expect.poll(() => asked).toEqual(["0", "1", "2"]);
  await page.clock.runFor(30_000);
  await expect.poll(() => asked).toEqual(["0", "1", "2", "3"]);

  // Then no timer: ten quiet minutes ask nothing (the old 60 s clock asked
  // ten more times, and the event stream's fallback poll every 15 s).
  await page.clock.runFor(10 * 60_000);
  await expect(frame.locator("img")).toHaveCount(0);
  expect(asked).toEqual(["0", "1", "2", "3"]);

  // A cover that arrives late still shows on the next library revision.
  exists = true;
  await page.evaluate(() =>
    window.dispatchEvent(new Event("music-artwork-retry")),
  );
  await expect(frame.locator("img")).toBeVisible();
  await expect
    .poll(() =>
      frame
        .locator("img")
        .evaluate((img: HTMLImageElement) => img.naturalWidth),
    )
    .toBeGreaterThan(0);
  expect(asked).toEqual(["0", "1", "2", "3", "4"]);
});

test("#175: the sign-in screen runs no artwork timer", async ({ page }) => {
  await page.addInitScript(() => {
    const seen: number[] = [];
    (window as unknown as { intervals: number[] }).intervals = seen;
    const original = window.setInterval.bind(window);
    window.setInterval = ((handler: TimerHandler, ms?: number) => {
      seen.push(ms ?? 0);
      return original(handler, ms);
    }) as typeof window.setInterval;
  });
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/setup/owner")
      return route.fulfill({ json: { setup_required: false } });
    return route.fulfill({ status: 401, json: { detail: "Sign in" } });
  });
  await page.goto("/");
  await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  const intervals = await page.evaluate(
    () => (window as unknown as { intervals: number[] }).intervals,
  );
  expect(intervals).not.toContain(60000);
});
