import { expect, test } from "@playwright/test";
import { album, mockApi } from "./mock";

// #97: a favourite that fails to save says so instead of failing silently.
test("a failed favourite shows its error on the song", async ({ page }) => {
  await mockApi(page, {
    answers: {
      "/v1/favourites": (route) =>
        "favourite" in route.request().postDataJSON()
          ? route.fulfill({
              status: 503,
              json: {
                detail: "Favourites are unavailable right now",
                code: "request_failed",
              },
            })
          : route.fulfill({ json: { favourited: false } }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page
    .getByRole("button", { name: "More options for Von dutch" })
    .click();
  await page.getByRole("menuitem", { name: "Favourite" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Favourites" }),
  ).toContainText("Favourites are unavailable right now");
  // A failure is never also said as a success.
  await expect(page.locator(".notice-area")).not.toContainText("favourites");
});

// #246: one icon for each meaning. A song in your library shows the tick
// ("In your library"), never a cloud; favourite is a star; the Downloads tab
// is the circled arrow.
test("library songs, favourites and Downloads use the agreed icons", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/favourites": (route) =>
        route.fulfill({ json: { favourited: true } }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  const row = page.locator(".song-row", { hasText: "Von dutch" });
  const mark = row.getByRole("img", { name: "In your library" });
  await expect(mark.locator("svg.lucide-circle-check")).toHaveCount(1);
  await expect(row.locator("svg.lucide-cloud")).toHaveCount(0);
  await expect(row.getByRole("button", { name: /to library/ })).toHaveCount(0);
  await row.getByRole("button", { name: "More options for Von dutch" }).click();
  const favourite = page.getByRole("menuitem", {
    name: "Remove from favourites",
  });
  await expect(favourite.locator("svg.lucide-star")).toHaveCount(1);
  await expect(favourite.locator("svg.lucide-heart")).toHaveCount(0);
  await page.keyboard.press("Escape");
  const downloads = page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "Downloads" });
  await expect(downloads.locator("svg.lucide-circle-arrow-down")).toHaveCount(
    1,
  );
});

// #97: a removal error that isn't plain text still reads as a sentence.
test("Remove from server shows a readable error for any answer", async ({
  page,
}) => {
  page.on("dialog", (dialog) => void dialog.accept());
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/tracks/t1/remove": (route) =>
        route.fulfill({
          status: 409,
          json: { detail: { reason: "busy" }, code: "conflict" },
        }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page
    .getByRole("button", { name: "More options for Von dutch" })
    .click();
  const menu = page.getByRole("menu", { name: "Von dutch", exact: true });
  // #249: the owner's item is a menu item with its icon, and comes last.
  const items = menu.getByRole("menuitem");
  await expect(items.last()).toHaveText("Remove from server");
  await expect(items.last().locator("svg.lucide-trash-2")).toHaveCount(1);
  await page.keyboard.press("End");
  await expect(items.last()).toBeFocused();
  await menu.getByRole("menuitem", { name: "Remove from server" }).click();
  const alert = menu.getByRole("alert");
  await expect(alert).toContainText("Something went wrong");
  await expect(alert).not.toContainText("[object");
});

// #246 (Codex): the server's answer decides a row's mark after Add to
// library. A skipped song keeps its Add button unless the library has it;
// a song already on its way shows the clock until the library confirms it,
// never the tick on trust.
test("Add to library marks the row from the server's answer", async ({
  page,
}) => {
  let looked = 0;
  const household = album.tracks.map((t) => ({
    ...t,
    server_state: "household_available",
  }));
  await mockApi(page, {
    answers: {
      "/v1/albums/album-1": (route) =>
        route.fulfill({ json: { ...album, tracks: household } }),
      "/v1/requests": (route) => {
        if (route.request().method() !== "POST")
          return route.fulfill({
            json: { items: [], next_offset: null, active: 0 },
          });
        const title = route.request().postDataJSON().tracks[0].title;
        return route.fulfill({
          json:
            title === "Von dutch"
              ? { created: 0, existing: 0, skipped: 1, downloads_enabled: true }
              : {
                  created: 0,
                  existing: 1,
                  skipped: 0,
                  downloads_enabled: true,
                },
        });
      },
      "/v1/library/lookup": (route) => {
        looked += 1;
        return route.fulfill({ json: { tracks: [null] } });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  const refused = page.locator(".song-row", { hasText: "Von dutch" });
  await refused
    .getByRole("button", { name: "Add Von dutch to library", exact: true })
    .click();
  await expect.poll(() => looked).toBe(1);
  await expect(
    refused.getByRole("button", { name: "Add Von dutch to library" }),
  ).toBeVisible();
  await expect(
    refused.getByRole("img", { name: "In your library" }),
  ).toHaveCount(0);
  const joined = page.locator(".song-row", { hasText: "360" });
  await joined
    .getByRole("button", { name: "Add 360 to library", exact: true })
    .click();
  await expect(joined.getByRole("img", { name: "Adding" })).toBeVisible();
  await expect(
    joined.getByRole("img", { name: "In your library" }),
  ).toHaveCount(0);
});
