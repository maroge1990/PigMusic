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
  await menu.getByRole("button", { name: "Remove from server…" }).click();
  const alert = menu.getByRole("alert");
  await expect(alert).toContainText("Something went wrong");
  await expect(alert).not.toContainText("[object");
});
