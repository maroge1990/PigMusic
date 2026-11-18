import { expect, test, type Route } from "@playwright/test";
import { mockApi } from "./mock";

// Codex confirmation round (#250, #183): nothing deferred runs after the
// session it was made in. Signing out sends it first, under that session.

test("signing out sends a playlist delete still waiting for its Undo time first", async ({
  page,
}) => {
  await page.clock.install();
  const calls: string[] = [];
  await mockApi(page, {
    answers: {
      "/v1/playlists/p1": (route: Route) => {
        if (route.request().method() === "DELETE") calls.push("DELETE p1");
        return route.fulfill({
          json: { id: "p1", name: "Road trip", revision: 1, entries: [] },
        });
      },
      "/v1/auth/logout": (route: Route) => {
        calls.push("logout");
        return route.fulfill({ json: {} });
      },
    },
  });
  await page.goto("/#/library/playlist/p1");
  await page.getByText("Playlist options").click();
  await page.getByRole("button", { name: "Delete playlist" }).click();
  await expect(page.locator(".notice-area")).toContainText(
    "Road trip deleted.",
  );
  // Within the app: the page stays, so nothing is sent as it goes away.
  await page.evaluate(() => (location.hash = "/account"));
  await page.getByRole("button", { name: "Sign out of PigMusic" }).click();
  await expect.poll(() => calls).toEqual(["DELETE p1", "logout"]);
  // Nothing is left to run later under someone else's sign-in.
  await page.clock.runFor(20_000);
  expect(calls).toEqual(["DELETE p1", "logout"]);
});
