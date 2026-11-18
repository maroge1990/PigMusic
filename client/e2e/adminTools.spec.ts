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
  const sam = page.locator(".person-row", { hasText: "Sam" });
  await expect(sam).toContainText("Member");
  await expect(sam.locator(".person-avatar")).toHaveText("S");
  await expect(page.getByText(/Sam · login succeeded/)).toBeVisible();
  // The owner's own account has no reset or disable.
  await expect(
    page.getByRole("button", { name: "More actions for Pat" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Add person" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Alex");
  await page.getByLabel("Username").fill("alex");
  await page.getByRole("button", { name: "Add", exact: true }).click();
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
  await page.getByRole("button", { name: "More actions for Sam" }).click();
  await page.getByRole("menuitem", { name: "Enable" }).click();
  await expect(page.getByRole("status").first()).toContainText(
    "Account enabled",
  );
  expect(sent).toEqual([{ disabled: false }]);
  expect(asked).toEqual([]);
  await page.getByRole("button", { name: "More actions for Sam" }).click();
  await expect(page.getByRole("menuitem", { name: "Disable" })).toBeVisible();
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

test("Remove from server acts at once and Undo puts the song back", async ({
  page,
}) => {
  // D-79 (#183): no dialog; the song is removed for everyone at once and
  // Undo restores what went to Trash.
  const removed: string[] = [];
  const restored: unknown[] = [];
  let libraryReads = 0;
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/tracks/t1/remove": (route: Route) => {
        removed.push(route.request().method());
        return route.fulfill({
          json: { id: "r1", status: "removed", restorable: ["r1"] },
        });
      },
      "/v1/admin/library/trash/restore": (route: Route) => {
        restored.push(route.request().postDataJSON());
        return route.fulfill({ json: { restored: ["r1"], failed: [] } });
      },
    },
  });
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/v1/library")
      libraryReads += 1;
  });
  const asked: string[] = [];
  page.on("dialog", (dialog) => {
    asked.push(dialog.message());
    void dialog.dismiss();
  });
  await page.goto(`/#/library/album/${album.id}`);
  const open = page.getByRole("button", { name: "More options for Von dutch" });
  const menu = page.getByRole("menu", { name: "Von dutch" });
  await open.click();
  const before = libraryReads;
  await menu.getByRole("menuitem", { name: "Remove from server" }).click();
  await expect.poll(() => removed).toEqual(["POST"]);
  expect(asked).toEqual([]);
  // Every open library view reads the library again.
  await expect.poll(() => libraryReads).toBeGreaterThan(before);
  const undo = page.locator(".notice-area");
  await expect(undo).toContainText(
    "Von dutch removed from the server for everyone.",
  );
  await undo.getByRole("button", { name: "Undo" }).click();
  await expect.poll(() => restored).toEqual([{ identities: ["r1"] }]);
  await expect(undo).toContainText("Von dutch is back.");
});

test("removing an album from the server returns to the albums, with Undo", async ({
  page,
}) => {
  const removed: string[] = [];
  const restored: unknown[] = [];
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/albums/album-1/remove": (route: Route) => {
        removed.push(route.request().method());
        return route.fulfill({
          json: { removed: 2, restorable: ["r1", "r2"] },
        });
      },
      "/v1/admin/library/trash/restore": (route: Route) => {
        restored.push(route.request().postDataJSON());
        return route.fulfill({ json: { restored: ["r1"], failed: ["r2"] } });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  const tools = page.locator("details.owner-tools");
  await tools.locator("summary").click();
  await tools.getByRole("button", { name: "Remove from server" }).click();
  await expect.poll(() => removed).toEqual(["POST"]);
  await expect(page).toHaveURL(/#\/library\/albums$/);
  // The Undo stays after leaving the album, and a song that can't come back
  // is said plainly.
  const undo = page.locator(".notice-area");
  await undo.getByRole("button", { name: "Undo" }).click();
  await expect.poll(() => restored).toEqual([{ identities: ["r1", "r2"] }]);
  await expect(undo).toContainText(
    "1 of 2 songs couldn't be put back; they're still in Removed music.",
  );
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
  await expect(page.locator(".notice-area")).toContainText("Song restored.");
  // #154: centred on the page, not offset by a sidebar the layout no longer has.
  const toast = await page.locator(".notice-area").boundingBox();
  const width = page.viewportSize()!.width;
  expect(Math.abs(toast!.x + toast!.width / 2 - width / 2)).toBeLessThan(2);
  await expect(panel).not.toContainText("Dreams");
  // A restore the server refuses keeps the song listed and says so.
  await row("Landslide").getByRole("button", { name: "Restore" }).click();
  await expect(page.locator(".notice-area")).toContainText(
    "Restore stopped. Check the library folder.",
  );
  await expect(panel).toContainText("Landslide");
  expect(restored).toEqual(["r1", "r2"]);
});

test("an album removal that stops part way stays, says why and offers Undo", async ({
  page,
}) => {
  // Codex, #183 round 2 (medium): what did move can still be undone.
  const restored: unknown[] = [];
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/albums/album-1/remove": (route: Route) =>
        route.fulfill({
          json: {
            removed: 1,
            restorable: ["r1"],
            stopped:
              "Removed 1 songs; remaining files need review. All removed songs are recoverable.",
          },
        }),
      "/v1/admin/library/trash/restore": (route: Route) => {
        restored.push(route.request().postDataJSON());
        return route.fulfill({ json: { restored: ["r1"], failed: [] } });
      },
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  const tools = page.locator("details.owner-tools");
  await tools.locator("summary").click();
  await tools.getByRole("button", { name: "Remove from server" }).click();
  await expect(tools.getByRole("alert")).toContainText(
    "remaining files need review",
  );
  await expect(page).toHaveURL(new RegExp(`album/${album.id}$`));
  const undo = page.locator(".notice-area");
  await expect(undo).toContainText("Part of");
  await undo.getByRole("button", { name: "Undo" }).click();
  await expect.poll(() => restored).toEqual([{ identities: ["r1"] }]);
});

test("D-120: People sits beside Unassigned music, each album with its cover", async ({
  page,
}) => {
  const assigned: unknown[] = [];
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/people": (route: Route) => route.fulfill({ json: people(1) }),
      "/v1/people/activity": (route: Route) =>
        route.fulfill({ json: { events: [], warnings: [] } }),
      "/v1/admin/unassigned": (route: Route) =>
        route.fulfill({
          json: {
            albums: [
              {
                id: album.id,
                title: "Brat",
                artist: album.artist,
                tracks: album.tracks.map((t) => ({
                  ...t,
                  cover_url: album.cover_url,
                })),
              },
            ],
            total: 1,
            next_offset: null,
          },
        }),
      "/v1/admin/unassigned/assign": (route: Route) => {
        assigned.push(route.request().postDataJSON());
        return route.fulfill({ json: { assigned: 2 } });
      },
    },
  });
  await page.goto("/#/admin/people");
  const peopleList = page.getByRole("region", { name: "People" });
  await expect(peopleList.locator(".person-row")).toHaveCount(2);
  await expect(peopleList.locator(".person-row").first()).toContainText(
    "Owner",
  );
  // A disabled person reads Disabled and can't be given music.
  await expect(peopleList.locator(".person-row").nth(1)).toContainText(
    "Disabled",
  );
  const inbox = page.getByRole("region", { name: /Unassigned music/ });
  await expect(inbox.getByRole("heading")).toHaveText(
    "Unassigned music1 album",
  );
  await expect(inbox.locator(".cover img")).toHaveCount(1);
  await expect(inbox).toContainText(`${album.artist} · 2 songs`);
  await expect(inbox.getByRole("checkbox")).toHaveCount(1);
  await inbox.getByRole("checkbox", { name: "Pat" }).check();
  await inbox.getByRole("button", { name: "Assign Brat" }).click();
  await expect
    .poll(() => assigned)
    .toEqual([{ track_ids: ["t1", "t2"], user_ids: ["o"] }]);
  // No explanatory paragraph (D-101).
  await expect(page.getByText("Newly scanned music waits")).toHaveCount(0);
});

test("D-120: the queue shows each song's cover, who asked and its progress", async ({
  page,
}) => {
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/people": (route: Route) => route.fulfill({ json: people() }),
      "/v1/admin/queue": (route: Route) =>
        route.fulfill({
          json: {
            library_revision: "1",
            downloads_enabled: true,
            items: [
              {
                id: "q1",
                status: "downloading",
                title: "One",
                artist: "A",
                album: "X",
                musicbrainz_release_id: "rel-x",
                requested_by: ["s"],
                received_bytes: 512 * 1024,
                total_bytes: 1024 * 1024,
              },
              {
                id: "q2",
                status: "waiting_for_sources",
                title: "Two",
                artist: "A",
                album: "X",
                requested_by: ["o"],
              },
              {
                id: "q3",
                status: "failed",
                title: "Three",
                artist: "A",
                album: "X",
              },
            ],
          },
        }),
      "/v1/catalogue/artwork/release/rel-x": (route: Route) =>
        route.fulfill({
          contentType: "image/svg+xml",
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"/>',
        }),
    },
  });
  await page.goto("/#/admin/downloads");
  const rows = page.locator(".queue-row");
  await expect(rows).toHaveCount(3);
  for (const row of await rows.all())
    await expect(row.locator(".cover")).toHaveCount(1);
  await expect(rows.nth(0).locator(".cover img")).toHaveCount(1);
  await expect(rows.nth(0).locator(".queue-asker")).toHaveText("Sam");
  await expect(rows.nth(1).locator(".queue-asker")).toHaveText("Pat");
  await expect(
    rows.nth(0).getByRole("progressbar", { name: "One: 50 percent" }),
  ).toBeVisible();
  await expect(rows.nth(1)).toContainText("Waiting");
  await expect(rows.nth(2)).toContainText("Couldn't add");
  await expect(
    rows.nth(2).getByRole("button", { name: "Try again" }),
  ).toBeVisible();
});

test("D-120: each Server library tool is a card with its icon", async ({
  page,
}) => {
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/library/trash": (route: Route) => route.fulfill({ json: [] }),
      "/v1/admin/library/organisation/history": (route: Route) =>
        route.fulfill({ json: [] }),
    },
  });
  await page.goto("/#/admin/library");
  const tools = page.locator(".library-tools > .library-tool");
  await expect(tools.locator("h2")).toHaveText([
    "Library scan",
    "Album editions",
    "Duplicate songs",
    "Removed music",
    "Organise existing music",
  ]);
  for (const h2 of await tools.locator("h2").all())
    await expect(h2.locator("svg")).toHaveCount(1);
});
