import { expect, test, type Page, type Route } from "@playwright/test";
import { album, artist, mockApi } from "./mock";

const photo = `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><rect width="512" height="512" fill="#888"/></svg>`;

/** Runs the paused clock in 2 s steps until `count()` reaches `want`, at
 * most `limit` ms: a look is scheduled only once the last 404 is back, so a
 * single jump could pass it. Returns the fake time it took. */
async function runUntil(
  page: Page,
  count: () => number,
  want: number,
  limit = 700_000,
) {
  let spent = 0;
  while (count() < want && spent < limit) {
    await page.clock.runFor(2_000);
    spent += 2_000;
    await page.waitForTimeout(5);
  }
  await expect.poll(count).toBe(want);
  return spent;
}

test("#175: a missing cover looks again five times over twelve minutes, then only when the library changes", async ({
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
  await page.clock.pauseAt(Date.now() + 1_000);

  // The server fetches a missing cover in the background: bounded looks.
  const looks = [2_000, 6_000, 30_000, 120_000, 600_000];
  for (const [i, wait] of looks.entries()) {
    const spent = await runUntil(page, () => asked.length, i + 2);
    expect(spent, `look ${i + 1}`).toBeGreaterThanOrEqual(wait);
    expect(spent, `look ${i + 1}`).toBeLessThanOrEqual(wait + 4_000);
  }
  expect(asked).toEqual(["0", "1", "2", "3", "4", "5"]);

  // Then no timer: ten quiet minutes ask nothing (the old 60 s clock asked
  // ten more times, and the event stream's fallback poll every 15 s).
  await page.clock.runFor(10 * 60_000);
  await expect(frame.locator("img")).toHaveCount(0);
  expect(asked).toHaveLength(6);

  // A cover that arrives later still shows on the next library revision.
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
  expect(asked).toHaveLength(7);
});

test("#175 (Codex round 1): a catalogue cover fetched after 38 s shows without a library change", async ({
  page,
}) => {
  await page.clock.install();
  let exists = false;
  await mockApi(page, {
    answers: {
      "/v1/albums/album-2": (route) =>
        route.fulfill({
          json: { ...album, id: "album-2", cover_url: "/api/v1/artwork/slow" },
        }),
      "/v1/artwork/slow": (route) =>
        exists
          ? route.fulfill({ contentType: "image/svg+xml", body: photo })
          : route.fulfill({ status: 404, json: { detail: "Not found" } }),
    },
  });
  const asked: string[] = [];
  page.on("request", (request) => asked.push(new URL(request.url()).pathname));
  await page.goto("/#/library/album/album-2");
  const frame = page.locator("main .cover").first();
  await expect(frame.locator("img")).toHaveCount(0);
  await page.clock.pauseAt(Date.now() + 1_000);
  const asks = () =>
    asked.filter((path) => path.startsWith("/api/v1/artwork/slow")).length;
  await runUntil(page, asks, 4);
  // The fetch finishes 45 s in; no library revision follows.
  exists = true;
  await runUntil(page, asks, 5, 130_000);
  await expect(frame.locator("img")).toBeVisible();
});

test("#175 (Codex round 1): returning to a cover after another starts its looks afresh", async ({
  page,
}) => {
  await page.clock.install();
  const asked: string[] = [];
  await mockApi(page, {
    answers: {
      "/v1/albums/album-2": (route) =>
        route.fulfill({
          json: {
            ...album,
            id: "album-2",
            cover_url: "/api/v1/artwork/b",
            tracks: album.tracks.map((t, i) => ({
              ...t,
              cover_url: i ? "/api/v1/artwork/b" : "/api/v1/artwork/a",
            })),
          },
        }),
      "/v1/artwork/a": (route) => {
        asked.push(new URL(route.request().url()).searchParams.get("r")!);
        return route.fulfill({ status: 404, json: { detail: "Not found" } });
      },
      "/v1/artwork/b": (route) =>
        route.fulfill({ contentType: "image/svg+xml", body: photo }),
    },
  });
  await page.goto("/#/library/album/album-2");
  const mini = page.locator(".mini-player .cover");
  await page.clock.pauseAt(Date.now() + 1_000);
  await page.getByText("Von dutch").first().click();
  await expect(mini).toBeVisible();
  await expect.poll(() => asked.length).toBe(1);
  await expect(mini.locator("img")).toHaveCount(0);
  // B loads in the same mini player cover, then A comes back: A is asked
  // for at once (the old state kept A failed and asked nothing).
  await page.getByText("360").first().click();
  await expect(mini.locator("img")).toBeVisible();
  await page.getByText("Von dutch").first().click();
  await expect.poll(() => asked.length).toBe(2);
  // And its looks start again from the first (2 s, not the 6 s second look).
  await expect(mini.locator("img")).toHaveCount(0);
  await page.waitForTimeout(100);
  await page.clock.runFor(2_000);
  await expect.poll(() => asked.length).toBe(3);
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

test("#175: an idle artist download panel follows request events, not a minute timer", async ({
  page,
}) => {
  await page.clock.install();
  // A connected, quiet event stream: no fallback poll stands in for it.
  await page.addInitScript(() => {
    class Quiet extends EventTarget {
      static CLOSED = 2;
      readyState = 1;
      onerror: ((event: Event) => void) | null = null;
      onopen: ((event: Event) => void) | null = null;
      close() {}
    }
    (window as unknown as { EventSource: unknown }).EventSource = Quiet;
  });
  let asked = 0;
  await mockApi(page, {
    role: "owner",
    answers: {
      [`/v1/admin/catalogue/artists/${artist.id}/acquisition`]: (route) => {
        asked += 1;
        return route.fulfill({
          json: { status: "idle", monitor: false, kinds: ["album"] },
        });
      },
    },
  });
  await page.goto(`/#/search/artist/${artist.id}?name=Charli%20xcx`);
  await expect.poll(() => asked).toBeGreaterThan(0);
  const first = asked;
  await page.clock.runFor(5 * 60_000);
  expect(asked).toBe(first);
  await page.evaluate(() =>
    window.dispatchEvent(new Event("music-requests-changed")),
  );
  await expect.poll(() => asked).toBe(first + 1);
});
