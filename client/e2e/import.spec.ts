import { expect, test, type Route } from "@playwright/test";
import { mockApi } from "./mock";

const preview = {
  playlists: 2,
  tracks: 30,
  saved: 10,
  household: 5,
  missing: 15,
};
const migration = {
  source_name: "Library.xml",
  imported_at: "2026-10-21T00:00:00Z",
  albums: [],
  stats: { tracks: 30, saved: 10, missing: 20, review: 0, selected: 0 },
};
const exportFile = {
  name: "Library.xml",
  mimeType: "text/xml",
  buffer: Buffer.from('<?xml version="1.0"?><plist version="1.0"></plist>'),
};

test("Apple import previews first, then saves playlists without downloading", async ({
  page,
}) => {
  const posted: string[] = [];
  const record = (body: unknown) => (route: Route) => {
    posted.push(new URL(route.request().url()).pathname);
    return route.fulfill({ json: body });
  };
  await mockApi(page, {
    answers: {
      "/v1/imports/apple/preview": record(preview),
      "/v1/imports/apple": record({ playlists: 2, tracks: 30 }),
    },
  });
  await page.goto("/#/library/playlists");
  await page.getByLabel("Apple Music XML export").setInputFiles(exportFile);
  await expect(page.getByText("15 missing")).toBeVisible();
  // Nothing is imported until the person confirms; members see no Admin path.
  expect(posted).toEqual(["/api/v1/imports/apple/preview"]);
  await expect(page.getByText("Admin: review the whole library")).toHaveCount(
    0,
  );
  await page.getByRole("button", { name: "Import 2 playlists" }).click();
  await expect(page.getByText("Imported 2 playlists")).toBeVisible();
  expect(posted).toEqual([
    "/api/v1/imports/apple/preview",
    "/api/v1/imports/apple",
  ]);
});

test("the owner hands the same export to the whole-library review", async ({
  page,
}) => {
  const posted: string[] = [];
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/imports/apple/preview": (route) => route.fulfill({ json: preview }),
      "/v1/admin/migration": (route) => route.fulfill({ json: migration }),
      "/v1/admin/migration/import": (route) => {
        posted.push(route.request().method());
        return route.fulfill({ json: migration });
      },
    },
  });
  await page.goto("/#/library/playlists");
  await page.getByLabel("Apple Music XML export").setInputFiles(exportFile);
  await page.getByText("Admin: review the whole library").click();
  await page.getByRole("button", { name: "Review whole library" }).click();
  await expect(page).toHaveURL(/#\/admin\/migration\?imported=1$/);
  expect(posted).toEqual(["POST"]);
  // The review shows the handed-over export, not the "start an import" state.
  await expect(page.getByRole("link", { name: "New import" })).toBeVisible();
});

test("a whole-library review whose session ends during its read sends nothing (Codex)", async ({
  page,
}) => {
  const posted: string[] = [];
  let answerRead: () => void = () => {};
  const read = new Promise<void>((resolve) => (answerRead = resolve));
  let reading = false;
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/imports/apple/preview": (route) => route.fulfill({ json: preview }),
      "/v1/admin/migration": async (route) => {
        reading = true;
        await read;
        // No selections: no question is asked, so only the session check
        // stands between this answer and the import.
        return route.fulfill({ json: migration });
      },
      "/v1/admin/migration/import": (route) => {
        posted.push(route.request().method());
        return route.fulfill({ json: migration });
      },
    },
  });
  await page.goto("/#/library/playlists");
  await page.getByLabel("Apple Music XML export").setInputFiles(exportFile);
  await page.getByText("Admin: review the whole library").click();
  await page.getByRole("button", { name: "Review whole library" }).click();
  await expect.poll(() => reading).toBe(true);
  // The session ends (revoked, or another tab signed in) mid-read.
  await page.evaluate(() =>
    window.dispatchEvent(new Event("music-auth-required")),
  );
  answerRead();
  await page.waitForTimeout(500);
  expect(posted).toEqual([]);
});
