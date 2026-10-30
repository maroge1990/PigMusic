import { expect, test } from "@playwright/test";
import { album, artist, mockApi } from "./mock";

// R19 smoke journeys at 1366 px and 375 px (projects), in both themes.
for (const appearance of ["light", "dark"] as const) {
  test(`home, library album and account render (${appearance})`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await mockApi(page, { appearance });
    await page.goto("/#/home");
    await expect(page.locator("html")).toHaveAttribute(
      "data-theme",
      appearance,
    );
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    await page.goto(`/#/library/album/${album.id}`);
    await expect(page.getByText("Von dutch").first()).toBeVisible();
    await page.goto("/#/account");
    await expect(
      page.getByRole("heading", { name: "Your account" }),
    ).toBeVisible();
    expect(errors).toEqual([]);
  });
}

test("the page never scrolls sideways", async ({ page }) => {
  await mockApi(page);
  for (const route of [
    "/#/home",
    `/#/library/album/${album.id}`,
    `/#/search/artist/${artist.id}?name=Charli%20xcx`,
  ]) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    );
    expect(overflow, route).toBeLessThanOrEqual(1);
  }
});

test("live updates reconnect after the stream gets an error response", async ({
  page,
}) => {
  let opened = 0;
  await mockApi(page, {
    answers: {
      "/v1/events": (route) => {
        opened += 1;
        if (opened === 1)
          return route.fulfill({ status: 502, body: "Bad gateway" });
        return route.fulfill({
          contentType: "text/event-stream",
          body: "retry: 600000\n: ok\n\n",
        });
      },
    },
  });
  await page.goto("/#/home");
  // Browsers never retry a 502 themselves (#73); the app opens a new stream.
  await expect.poll(() => opened, { timeout: 10000 }).toBeGreaterThan(1);
});
