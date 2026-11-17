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
  await expect(page.getByRole("alert")).toContainText(
    "Favourites are unavailable right now",
  );
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
  const menu = page.getByRole("menu", { name: "Von dutch" });
  await menu.getByRole("button", { name: "Remove from server" }).click();
  const alert = menu.getByRole("alert");
  await expect(alert).toContainText("Something went wrong");
  await expect(alert).not.toContainText("[object");
});
