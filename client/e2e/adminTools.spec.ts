import { expect, test, type Route } from "@playwright/test";
import { album, mockApi } from "./mock";

// #97 (test gaps): the owner's tools that change things for everyone.

const people = (disabled = 0) => ({
  people: [
    { id: "o", display_name: "Pat", role: "owner", disabled: 0 },
    { id: "s", display_name: "Sam", role: "member", disabled },
  ],
});

test("the owner adds a person and is shown their one-use invite", async ({
  page,
}) => {
  let added: unknown = null;
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/people": (route: Route) => {
        if (route.request().method() === "POST") {
          added = route.request().postDataJSON();
          return route.fulfill({
            json: {
              user_id: "a",
              code: "SETUP-123",
              expires_at: Date.now() / 1000 + 86400,
              invite_url: null,
            },
          });
        }
        return route.fulfill({ json: people() });
      },
      "/v1/people/activity": (route: Route) =>
        route.fulfill({
          json: {
            events: [
              {
                at: Date.now() / 1000 - 60,
                display_name: "Sam",
                event: "login_succeeded",
              },
            ],
            warnings: [],
          },
        }),
      "/v1/admin/unassigned": (route: Route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
    },
  });
  await page.goto("/#/admin/people");
  await expect(page.getByText("Sam · member")).toBeVisible();
  await expect(page.getByText(/Sam · login succeeded/)).toBeVisible();
  // The owner's own account has no reset or disable.
  await expect(page.getByRole("button", { name: "Disable Pat" })).toHaveCount(
    0,
  );
  await page.getByLabel("Name", { exact: true }).fill("Alex");
  await page.getByLabel("Username").fill("alex");
  await page.getByRole("button", { name: "Add person" }).click();
  const invite = page.getByRole("region", { name: "Invite for Alex" });
  await expect(invite).toContainText("Setup code: SETUP-123");
  await expect(page.getByRole("status").first()).toContainText("Person added");
  expect(added).toEqual({ username: "alex", display_name: "Alex" });
  await invite.getByRole("button", { name: "Done" }).click();
  await expect(invite).toHaveCount(0);
});

test("enabling a disabled account doesn't ask first", async ({ page }) => {
  const sent: unknown[] = [];
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/people": (route: Route) =>
        route.fulfill({ json: people(sent.length ? 0 : 1) }),
      "/v1/people/activity": (route: Route) =>
        route.fulfill({ json: { events: [], warnings: [] } }),
      "/v1/admin/unassigned": (route: Route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
      "/v1/people/s/disabled": (route: Route) => {
        sent.push(route.request().postDataJSON());
        return route.fulfill({ json: { disabled: false } });
      },
    },
  });
  const asked: string[] = [];
  page.on("dialog", (dialog) => {
    asked.push(dialog.message());
    void dialog.dismiss();
  });
  await page.goto("/#/admin/people");
  await page.getByRole("button", { name: "Enable Sam" }).click();
  await expect(page.getByRole("status").first()).toContainText(
    "Account enabled",
  );
  expect(sent).toEqual([{ disabled: false }]);
  expect(asked).toEqual([]);
  await expect(page.getByRole("button", { name: "Disable Sam" })).toBeVisible();
});

test("People warns about wrong passwords tried against an account", async ({
  page,
}) => {
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/people": (route: Route) => route.fulfill({ json: people() }),
      "/v1/people/activity": (route: Route) =>
        route.fulfill({
          json: {
            events: [],
            warnings: [
              {
                at: Date.now() / 1000 - 3600,
                display_name: "Sam",
                event: "sign_in_slowed",
              },
            ],
          },
        }),
      "/v1/admin/unassigned": (route: Route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
    },
  });
  await page.goto("/#/admin/people");
  await expect(
    page.getByText(
      "Someone has been trying wrong passwords for Sam this week.",
    ),
  ).toBeVisible();
});

test("Remove from server asks first, then removes the song for everyone", async ({
  page,
}) => {
  const removed: string[] = [];
  let libraryReads = 0;
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/tracks/t1/remove": (route: Route) => {
        removed.push(route.request().method());
        return route.fulfill({ json: { removed: true } });
      },
    },
  });
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/v1/library")
      libraryReads += 1;
  });
  const asked: string[] = [];
  let answer = false;
  page.on("dialog", (dialog) => {
    asked.push(dialog.message());
    void (answer ? dialog.accept() : dialog.dismiss());
  });
  await page.goto(`/#/library/album/${album.id}`);
  const open = page.getByRole("button", { name: "More options for Von dutch" });
  const menu = page.getByRole("menu", { name: "Von dutch" });
  await open.click();
  await menu.getByRole("button", { name: "Remove from server…" }).click();
  await expect.poll(() => asked.length).toBe(1);
  expect(asked[0]).toBe(
    "Move Von dutch to recoverable trash on the server? It is removed for everyone.",
  );
  expect(removed).toEqual([]);
  answer = true;
  const before = libraryReads;
  if (!(await menu.isVisible())) await open.click();
  await menu.getByRole("button", { name: "Remove from server…" }).click();
  await expect.poll(() => removed).toEqual(["POST"]);
  // Every open library view reads the library again.
  await expect.poll(() => libraryReads).toBeGreaterThan(before);
  await expect(menu.getByRole("alert")).toHaveCount(0);
});

test("removing an album from the server returns to the albums", async ({
  page,
}) => {
  const removed: string[] = [];
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/albums/album-1/remove": (route: Route) => {
        removed.push(route.request().method());
        return route.fulfill({ json: { removed: true } });
      },
    },
  });
  page.on("dialog", (dialog) => void dialog.accept());
  await page.goto(`/#/library/album/${album.id}`);
  const tools = page.locator("details.owner-tools");
  await tools.locator("summary").click();
  await tools.getByRole("button", { name: "Remove from server…" }).click();
  await expect.poll(() => removed).toEqual(["POST"]);
  await expect(page).toHaveURL(/#\/library\/albums$/);
});

test("Removed music lists songs and restores one", async ({ page }) => {
  let trash = [
    { id: "r1", track: { title: "Dreams", artist: "Fleetwood Mac" } },
    { id: "r2", track: { title: "Landslide", artist: "Fleetwood Mac" } },
  ].map((item) => ({ ...item, status: "trashed" }));
  const restored: string[] = [];
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/library/trash": (route: Route) =>
        route.fulfill({ json: trash }),
      "/v1/admin/library/trash/r1/restore": (route: Route) => {
        restored.push("r1");
        trash = trash.filter((item) => item.id !== "r1");
        return route.fulfill({ json: { restored: true } });
      },
      "/v1/admin/library/trash/r2/restore": (route: Route) => {
        restored.push("r2");
        return route.fulfill({
          status: 409,
          json: { detail: "The file is missing", code: "conflict" },
        });
      },
      "/v1/admin/library/organisation/history": (route: Route) =>
        route.fulfill({ json: [] }),
      "/v1/admin/unassigned": (route: Route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
      "/v1/admin/library/duplicates": (route: Route) =>
        route.fulfill({ json: { duplicates: [] } }),
    },
  });
  await page.goto("/#/admin/library");
  const panel = page
    .locator(".system-panel")
    .filter({ has: page.getByRole("heading", { name: "Removed music" }) });
  await expect(panel).toContainText("Dreams");
  await expect(panel).toContainText("Landslide");
  const row = (title: string) =>
    panel.locator(".result-row").filter({ hasText: title });
  await row("Dreams").getByRole("button", { name: "Restore" }).click();
  await expect(page.locator(".toast")).toContainText("Song restored.");
  // #154: centred on the page, not offset by a sidebar the layout no longer has.
  const toast = await page.locator(".toast").boundingBox();
  const width = page.viewportSize()!.width;
  expect(Math.abs(toast!.x + toast!.width / 2 - width / 2)).toBeLessThan(2);
  await expect(panel).not.toContainText("Dreams");
  // A restore the server refuses keeps the song listed and says so.
  await row("Landslide").getByRole("button", { name: "Restore" }).click();
  await expect(page.locator(".toast")).toContainText(
    "Restore stopped. Check the library folder.",
  );
  await expect(panel).toContainText("Landslide");
  expect(restored).toEqual(["r1", "r2"]);
});
