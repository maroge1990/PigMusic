import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

const queue = {
  library_revision: "1",
  downloads_enabled: false,
  items: [
    { id: "q1", status: "downloading", title: "One", artist: "A", album: "X" },
    {
      id: "q2",
      status: "waiting_for_sources",
      title: "Two",
      artist: "A",
      album: "X",
    },
    { id: "q3", status: "failed", title: "Three", artist: "A", album: "X" },
  ],
};

test("the owner's Admin overview shows the numbers to act on", async ({
  page,
}) => {
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/queue": (route) => route.fulfill({ json: queue }),
      "/v1/people": (route) =>
        route.fulfill({
          json: {
            people: [{ id: "1" }, { id: "2" }, { id: "3", disabled: true }],
          },
        }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [{}, {}], next_offset: null } }),
      "/v1/admin/mixes": (route) =>
        route.fulfill({
          json: {
            budget_bytes: 50000000000,
            reserve_bytes: 2000000000,
            profile: "mp3_320",
            timezone: "Australia/Melbourne",
            files: 10,
            used_bytes: 100000000,
            ready: 4,
            preparing: 1,
            retrying: 1,
            failed: 2,
            reclaimable_bytes: 12000000,
            issues: [{ mix: "Daily blend", message: "Downloads are paused" }],
            people: [
              { id: "u1", name: "Mark", ready: 3, preparing: 1 },
              { id: "u2", name: "Sam", ready: 1, preparing: 0 },
            ],
          },
        }),
      "/v1/admin/mixes/people/u1": (route) =>
        route.fulfill({
          json: {
            person: "Mark",
            editions: [
              {
                id: "e1",
                kind: "daily",
                day: "2026-10-18",
                title: "Daily blend",
                reason: "Discoveries inspired by your library",
                state: "preparing",
                active: false,
                attempts: 1,
                retry_at: null,
                worked_at: null,
                blocked: "Downloads are paused",
                published: null,
                songs: [
                  { title: "Ready One", artist: "A", status: "cached" },
                  {
                    title: "Missing Two",
                    artist: "B",
                    status: "failed",
                    detail: "No matching recording available",
                  },
                  { title: "Next Three", artist: "C", status: "next" },
                ],
              },
            ],
          },
        }),
      "/v1/admin/mixes/people/u2": (route) =>
        route.fulfill({ json: { person: "Sam", editions: [] } }),
      "/v1/admin/warmer": (route) =>
        route.fulfill({
          json: {
            artists_ready: 12,
            covers_prepared: 40,
            queued: 0,
            last_pass_finished: null,
          },
        }),
    },
  });
  await page.goto("/#/home");
  await expect(
    page.getByRole("link", { name: "Admin" }).first(),
  ).toBeAttached();
  await page.goto("/#/admin");
  const tile = (label: string) =>
    page
      .locator("a, div")
      .filter({ has: page.getByText(label, { exact: true }) })
      .last();
  await expect(tile("Queued or downloading")).toContainText("2");
  await expect(tile("Queued or downloading")).toContainText(
    "Downloads are paused",
  );
  await expect(tile("Failed requests")).toContainText("1");
  await expect(tile("Albums awaiting an owner")).toContainText("2");
  await expect(tile("People")).toContainText("2");
  await expect(tile("Artist pages ready")).toContainText("12");
  const mixes = page
    .locator(".system-panel")
    .filter({ has: page.getByRole("heading", { name: "Mix preparation" }) });
  await expect(mixes).toContainText("2 unavailable candidates");
  await expect(mixes).toContainText("12 MB can be reclaimed");
  await mixes.getByText("Preparation issues", { exact: true }).click();
  await expect(mixes).toContainText("Daily blend: Downloads are paused");
  await expect(mixes).toContainText("1/3 songs ready · 1 unavailable");
  await expect(mixes).toContainText("Waiting: Downloads are paused");
  await expect(mixes.locator("li[data-status=failed]")).toContainText(
    "No matching recording available",
  );
  await expect(mixes.locator("li[data-status=next]")).toContainText("Up next");
  await mixes.getByRole("button", { name: /Sam/ }).click();
  await expect(mixes).toContainText("No mixes are planned for Sam yet.");
  const budget = mixes.getByLabel("Cache budget (GB)");
  await budget.fill("55");
  await mixes.getByRole("button", { name: "Refresh mix status" }).click();
  await expect(
    mixes.getByRole("button", { name: "Refresh mix status" }),
  ).toBeEnabled();
  await expect(budget).toHaveValue("55");
});

test("members have no Admin section, even by address", async ({ page }) => {
  await mockApi(page, { role: "member" });
  await page.goto("/#/admin");
  await expect(
    page.getByRole("heading", { name: "Home", level: 1 }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Admin" })).toHaveCount(0);
});

test("Admin follows the shell's event stream without a timer", async ({
  page,
}) => {
  // Frozen timers: nothing polls, so only the pushed `admin` event can move
  // the numbers after the first load.
  await page.clock.install();
  let queueAsked: () => void = () => {};
  const asked = new Promise<void>((resolve) => (queueAsked = resolve));
  let queueCalls = 0;
  const pushed = {
    library_revision: "1",
    downloads_enabled: true,
    items: [
      ...["a", "b", "c", "d"].map((id) => ({
        id,
        status: "downloading",
        title: id,
        artist: "A",
        album: "X",
      })),
      { id: "e", status: "failed", title: "e", artist: "A", album: "X" },
      { id: "f", status: "failed", title: "f", artist: "A", album: "X" },
    ],
  };
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/queue": (route) => {
        queueCalls += 1;
        queueAsked();
        return route.fulfill({ json: queue });
      },
      "/v1/events": async (route) => {
        await asked;
        await route.fulfill({
          contentType: "text/event-stream",
          body: `event: admin\ndata: ${JSON.stringify({
            queue: pushed,
            scan: { status: "completed" },
            soulseek: { configured: true, connected: true },
          })}\n\n`,
        });
      },
      "/v1/people": (route) => route.fulfill({ json: { people: [] } }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
      "/v1/admin/warmer": (route) =>
        route.fulfill({
          json: { artists_ready: 0, covers_prepared: 0, queued: 0 },
        }),
    },
  });
  await page.goto("/#/admin");
  const tile = (label: string) =>
    page
      .locator("a, div")
      .filter({ has: page.getByText(label, { exact: true }) })
      .last();
  await expect(tile("Queued or downloading")).toContainText("4");
  await expect(tile("Queued or downloading")).not.toContainText(
    "Downloads are paused",
  );
  await expect(tile("Failed requests")).toContainText("2");
  expect(queueCalls).toBe(1);
});

test("the owner reviews duplicate songs and moves them to Removed music", async ({
  page,
}) => {
  const duplicate = {
    track: { id: "dl", title: "Dreams", artist: "Fleetwood Mac" },
    album: "Greatest Hits",
    keeper: { id: "nas", title: "Dreams", artist: "Fleetwood Mac" },
    keeper_album: "Rumours",
  };
  let removed: string[] = [];
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/library/duplicates/remove": (route) => {
        removed = route.request().postDataJSON().track_ids;
        return route.fulfill({ json: { removed: 1, skipped: 0 } });
      },
      "/v1/admin/library/trash": (route) => route.fulfill({ json: [] }),
      "/v1/admin/library/organisation/history": (route) =>
        route.fulfill({ json: [] }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
      "/v1/admin/library/duplicates": (route) =>
        route.fulfill({
          json: { duplicates: removed.length ? [] : [duplicate] },
        }),
    },
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/#/admin/library");
  const panel = page
    .locator(".system-panel")
    .filter({ has: page.getByRole("heading", { name: "Duplicate songs" }) });
  await panel.getByRole("button", { name: "Find duplicates" }).click();
  await expect(panel).toContainText("Fleetwood Mac · Greatest Hits");
  await expect(panel).toContainText("Kept on Rumours");
  await expect(panel.getByRole("checkbox")).toBeChecked();
  await panel
    .getByRole("button", { name: "Move to Removed music · 1" })
    .click();
  await expect(panel).toContainText("No duplicate songs.");
  expect(removed).toEqual(["dl"]);
});

test("the owner tidies an album's editions and can undo it", async ({
  page,
}) => {
  const song = (id: string, title: string, album: string, quality: string) => ({
    id,
    title,
    album,
    quality,
  });
  const album = {
    id: "deluxe",
    artist: "The Killers",
    keep: { album_id: "deluxe", title: "Hot Fuss (Deluxe Edition)", songs: 4 },
    editions: [{ album_id: "standard", title: "Hot Fuss", songs: 3 }],
    replace: [
      {
        ...song("s-jenny", "Jenny", "Hot Fuss", "FLAC"),
        replaces: song(
          "d-jenny",
          "Jenny",
          "Hot Fuss (Deluxe Edition)",
          "MP3 128",
        ),
      },
    ],
    remove: [
      {
        ...song("s-mr", "Mr. Brightside", "Hot Fuss", "MP3 320"),
        kept: song(
          "d-mr",
          "Mr. Brightside",
          "Hot Fuss (Deluxe Edition)",
          "MP3 320",
        ),
      },
    ],
    leftovers: [
      song("s-glam", "Glamorous Indie Rock & Roll", "Hot Fuss", "MP3 320"),
    ],
    fingerprint: "f".repeat(64),
  };
  let tidied: { path: string; body: unknown } | null = null;
  let undone = false;
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/library/tidy/history/run/undo": (route) => {
        undone = true;
        return route.fulfill({ json: { id: "run", status: "undone" } });
      },
      "/v1/admin/library/tidy/deluxe": (route) => {
        tidied = {
          path: new URL(route.request().url()).pathname,
          body: route.request().postDataJSON(),
        };
        return route.fulfill({ json: { id: "run", status: "applied" } });
      },
      "/v1/admin/library/tidy": (route) =>
        route.fulfill({
          json:
            tidied && !undone
              ? {
                  albums: [],
                  history: [
                    {
                      id: "run",
                      artist: "The Killers",
                      title: "Hot Fuss (Deluxe Edition)",
                      time: 1,
                      status: "applied",
                    },
                  ],
                }
              : { albums: [album], history: [] },
        }),
      "/v1/admin/library/duplicates": (route) =>
        route.fulfill({ json: { duplicates: [] } }),
      "/v1/admin/library/trash": (route) => route.fulfill({ json: [] }),
      "/v1/admin/library/organisation/history": (route) =>
        route.fulfill({ json: [] }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
    },
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/#/admin/library");
  const panel = page
    .locator(".system-panel")
    .filter({ has: page.getByRole("heading", { name: "Album editions" }) });
  await panel.getByRole("button", { name: "Find editions" }).click();
  await expect(panel).toContainText("Keep Hot Fuss (Deluxe Edition) (4 songs)");
  await expect(panel).toContainText("FLAC from Hot Fuss replaces MP3 128");
  await expect(panel).toContainText("Removed from Hot Fuss · MP3 320 kept");
  await expect(panel).toContainText("Stays on Hot Fuss: review it yourself");
  await panel
    .getByRole("button", { name: "Tidy Hot Fuss (Deluxe Edition)" })
    .click();
  await expect(panel).toContainText("No albums on several editions.");
  expect(tidied).toEqual({
    path: "/api/v1/admin/library/tidy/deluxe",
    body: { fingerprint: "f".repeat(64) },
  });
  await panel.getByRole("button", { name: "Undo" }).click();
  await expect(panel).toContainText("Hot Fuss (Deluxe Edition)");
  expect(undone).toBe(true);
});

test("the owner joins an album split across two editions", async ({ page }) => {
  const join = (id: string, title: string, track: number) => ({
    id,
    title,
    album: "Dude Ranch",
    quality: "FLAC",
    album_id: "dude-1997",
    to: { track_number: track, disc_number: 1 },
  });
  const album = {
    id: "dude-2008",
    artist: "blink-182",
    keep: { album_id: "dude-2008", title: "Dude Ranch", songs: 13 },
    editions: [
      { album_id: "dude-1997", title: "Dude Ranch", year: 1997, songs: 2 },
    ],
    replace: [],
    remove: [],
    join: [join("dammit", "Dammit", 3), join("josie", "Josie", 11)],
    leftovers: [],
    fingerprint: "a".repeat(64),
  };
  let joined: unknown = null;
  let confirmed = "";
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/library/tidy/dude-2008": (route) => {
        joined = route.request().postDataJSON();
        return route.fulfill({ json: { id: "run", status: "applied" } });
      },
      "/v1/admin/library/tidy": (route) =>
        route.fulfill({
          json: joined
            ? { albums: [], history: [] }
            : { albums: [album], history: [] },
        }),
      "/v1/admin/library/duplicates": (route) =>
        route.fulfill({ json: { duplicates: [] } }),
      "/v1/admin/library/trash": (route) => route.fulfill({ json: [] }),
      "/v1/admin/library/organisation/history": (route) =>
        route.fulfill({ json: [] }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
    },
  });
  page.on("dialog", (dialog) => {
    confirmed = dialog.message();
    return dialog.accept();
  });
  await page.goto("/#/admin/library");
  const panel = page
    .locator(".system-panel")
    .filter({ has: page.getByRole("heading", { name: "Album editions" }) });
  await panel.getByRole("button", { name: "Find editions" }).click();
  await expect(panel).toContainText("Join 2 songs from Dude Ranch (1997)");
  await expect(panel).toContainText("FLAC becomes track 3 of Dude Ranch");
  await expect(panel).toContainText("FLAC becomes track 11 of Dude Ranch");
  await panel.getByRole("button", { name: "Tidy Dude Ranch" }).click();
  await expect(panel).toContainText("No albums on several editions.");
  expect(confirmed).toContain("join 2 songs into it");
  expect(joined).toEqual({ fingerprint: "a".repeat(64) });
});

test("a tidy-up or Undo that stops keeps its reason on screen", async ({
  page,
}) => {
  const song = { id: "s-mr", title: "Mr. Brightside", album: "Hot Fuss" };
  const album = {
    id: "deluxe",
    artist: "The Killers",
    keep: { album_id: "deluxe", title: "Hot Fuss (Deluxe Edition)", songs: 4 },
    editions: [{ album_id: "standard", title: "Hot Fuss", songs: 3 }],
    replace: [],
    remove: [
      {
        ...song,
        quality: "MP3 320",
        kept: { ...song, id: "d-mr", quality: "MP3 320" },
      },
    ],
    leftovers: [],
    fingerprint: "f".repeat(64),
  };
  let stopped = false;
  const stoppedRun = {
    id: "run",
    artist: "The Killers",
    title: "Hot Fuss (Deluxe Edition)",
    time: 1,
    status: "stopped",
    error: "Original file unavailable; removal stopped.",
  };
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/library/tidy/history/run/undo": (route) =>
        route.fulfill({
          status: 409,
          json: {
            detail:
              "Undo stopped: Organised file changed; undo stopped. Recoverable files are retained; check the album and try Undo again.",
          },
        }),
      "/v1/admin/library/tidy/deluxe": (route) => {
        stopped = true;
        return route.fulfill({
          status: 409,
          json: {
            detail:
              "Tidy-up stopped: Original file unavailable; removal stopped. Every file moved so far is recoverable; scan the library and review again.",
          },
        });
      },
      "/v1/admin/library/tidy": (route) =>
        route.fulfill({
          json: stopped
            ? { albums: [album], history: [stoppedRun] }
            : { albums: [album], history: [] },
        }),
      "/v1/admin/library/duplicates": (route) =>
        route.fulfill({ json: { duplicates: [] } }),
      "/v1/admin/library/trash": (route) => route.fulfill({ json: [] }),
      "/v1/admin/library/organisation/history": (route) =>
        route.fulfill({ json: [] }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
    },
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/#/admin/library");
  const panel = page
    .locator(".system-panel")
    .filter({ has: page.getByRole("heading", { name: "Album editions" }) });
  await panel.getByRole("button", { name: "Find editions" }).click();
  await panel
    .getByRole("button", { name: "Tidy Hot Fuss (Deluxe Edition)" })
    .click();
  // The refreshed review shows the stopped run, and the reason stays.
  await expect(panel).toContainText(
    "stopped part-way: Original file unavailable; removal stopped.",
  );
  await expect(panel.getByRole("alert")).toContainText(
    "Tidy-up stopped: Original file unavailable; removal stopped.",
  );
  await panel.getByRole("button", { name: "Undo" }).click();
  await expect(panel.getByRole("alert")).toContainText(
    "Undo stopped: Organised file changed; undo stopped.",
  );
  await expect(panel).toContainText("Tidied albums");
});

test("the owner settles changes an earlier tidy-up did not record", async ({
  page,
}) => {
  const run = {
    id: "run",
    artist: "Matthew McConaughey",
    title: "Interstellar (Deluxe Edition)",
    time: 1,
    status: "stopped",
    error:
      "Put back what this tidy-up recorded; 1 other change may belong to it. Include each that does, or mark it not part of this tidy-up, then Undo again.",
    unresolved: [
      {
        id: "removal-1",
        kind: "removal",
        title: "Cornfield Chase",
        album: "Interstellar",
        state: "in Removed music",
      },
    ],
  };
  let resolved: unknown = null;
  let settled = false;
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/library/tidy/history/run/unresolved/removal-1": (route) => {
        resolved = route.request().postDataJSON();
        settled = true;
        return route.fulfill({ json: { id: "run", status: "stopped" } });
      },
      "/v1/admin/library/tidy/history/run/undo": (route) =>
        route.fulfill({ status: 409, json: { detail: run.error } }),
      "/v1/admin/library/tidy": (route) =>
        route.fulfill({
          json: {
            albums: [],
            history: [settled ? { ...run, unresolved: [], error: null } : run],
          },
        }),
      "/v1/admin/library/duplicates": (route) =>
        route.fulfill({ json: { duplicates: [] } }),
      "/v1/admin/library/trash": (route) => route.fulfill({ json: [] }),
      "/v1/admin/library/organisation/history": (route) =>
        route.fulfill({ json: [] }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
    },
  });
  await page.goto("/#/admin/library");
  const panel = page
    .locator(".system-panel")
    .filter({ has: page.getByRole("heading", { name: "Album editions" }) });
  await panel.getByRole("button", { name: "Find editions" }).click();
  await expect(panel).toContainText(
    "Interstellar · in Removed music. Part of this tidy-up?",
  );
  // Undo does not claim the album is back while a change awaits review.
  await panel.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText(
    "1 other change may belong to it",
  );
  await panel.getByRole("button", { name: "Include in Undo" }).click();
  await expect(panel).not.toContainText("Part of this tidy-up?");
  expect(resolved).toEqual({ include: true });
});

test("the owner approves or rejects uploads credited only by the uploader", async ({
  page,
}) => {
  const review = {
    source: "soundcloud",
    provider_track_id: "artist/song",
    title: "Song",
    uploader: "Artist",
    duration: 201,
    url: "https://soundcloud.com/artist/song",
  };
  const held = {
    library_revision: "1",
    downloads_enabled: true,
    items: [
      {
        id: "held",
        status: "failed",
        title: "Song",
        artist: "Artist",
        album: "Album",
        source_review: review,
      },
      // A running search and a completed request are never offered.
      {
        id: "busy",
        status: "searching",
        title: "Busy",
        artist: "Artist",
        album: "Album",
        source_review: review,
      },
      {
        id: "done",
        status: "completed",
        title: "Done",
        artist: "Artist",
        album: "Album",
        source_review: review,
      },
    ],
  };
  const decisions: string[] = [];
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/queue": (route) => route.fulfill({ json: held }),
      "/v1/admin/queue/held/source-review/approve": (route) => {
        decisions.push(route.request().method());
        return route.fulfill({
          json: {
            ...held,
            items: [
              {
                ...held.items[0],
                status: "waiting_for_sources",
                source_review: null,
              },
            ],
          },
        });
      },
    },
  });
  await page.goto("/#/admin/downloads");
  const list = page.getByRole("region", { name: "Needs review" });
  await expect(list.locator(".result-row")).toHaveCount(1);
  await expect(list).toContainText("uploaded by Artist · 3:21");
  await expect(list.getByRole("link", { name: "Open upload" })).toHaveAttribute(
    "href",
    review.url,
  );
  await list.getByRole("button", { name: "Approve" }).click();
  expect(decisions).toEqual(["POST"]);
  await expect(page.getByRole("region", { name: "Needs review" })).toHaveCount(
    0,
  );
});

test("Reset password and Disable ask first, naming the person", async ({
  page,
}) => {
  const calls: string[] = [];
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/people": (route) =>
        route.fulfill({
          json: {
            people: [
              { id: "o", display_name: "Pat", role: "owner", disabled: 0 },
              { id: "s", display_name: "Sam", role: "member", disabled: 0 },
            ],
          },
        }),
      "/v1/people/activity": (route) =>
        route.fulfill({ json: { events: [], warnings: [] } }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
      "/v1/people/s/setup-code": (route) => {
        calls.push("reset");
        return route.fulfill({
          json: { code: "c", user_id: "s", expires_at: 0, invite_url: null },
        });
      },
      "/v1/people/s/disabled": (route) => {
        calls.push("disable");
        return route.fulfill({ json: { disabled: true } });
      },
    },
  });
  const asked: string[] = [];
  let answer = false;
  page.on("dialog", (dialog) => {
    asked.push(dialog.message());
    void (answer ? dialog.accept() : dialog.dismiss());
  });
  await page.goto("/#/admin/people");
  const reset = page.getByRole("button", { name: "Reset password for Sam" });
  const disable = page.getByRole("button", { name: "Disable Sam" });
  await reset.click();
  await disable.click();
  await expect.poll(() => asked.length).toBe(2);
  expect(asked[0]).toContain("Reset Sam's password?");
  expect(asked[1]).toContain("Disable Sam's account?");
  expect(calls).toEqual([]);
  answer = true;
  await reset.click();
  await expect.poll(() => calls).toEqual(["reset"]);
  await disable.click();
  await expect.poll(() => calls).toEqual(["reset", "disable"]);
});

test("organising runs in the background with progress and can be stopped", async ({
  page,
}) => {
  const item = (id: string) => ({
    id,
    title: "Song " + id,
    artist: "A",
    from: `A/X/${id}.mp3`,
    to: `A/X/01 ${id}.mp3`,
    rename: true,
    tag_supported: true,
    conflict: false,
  });
  let sent: unknown = null;
  let polls = 0;
  let stopped = false;
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/library/trash": (route) => route.fulfill({ json: [] }),
      "/v1/admin/library/organisation/history": (route) =>
        route.fulfill({ json: [] }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
      "/v1/admin/library/organisation": (route) => {
        if (route.request().method() === "POST") {
          sent = route.request().postDataJSON();
          return route.fulfill({
            json: { status: "running", total: 2, done: 0, message: null },
          });
        }
        return route.fulfill({
          json: { items: [item("s1"), item("s2")], note: "" },
        });
      },
      "/v1/admin/library/organisation/job": (route) => {
        polls += 1;
        return route.fulfill({
          json: !sent
            ? { status: "idle" }
            : stopped
              ? {
                  status: "stopped",
                  total: 2,
                  done: 1,
                  message: "Stopped; songs already done can be undone.",
                }
              : { status: "running", total: 2, done: 1, message: null },
        });
      },
      "/v1/admin/library/organisation/job/stop": (route) => {
        stopped = true;
        return route.fulfill({
          json: { status: "stopping", total: 2, done: 1, message: null },
        });
      },
    },
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/#/admin/library");
  const panel = page.locator(".system-panel").filter({
    has: page.getByRole("heading", { name: "Organise existing music" }),
  });
  await panel.getByRole("button", { name: "Review library" }).click();
  await panel.getByRole("button", { name: "Select eligible changes" }).click();
  await panel
    .getByRole("button", { name: "Apply reviewed changes · 2" })
    .click();
  await expect(panel.getByRole("status")).toContainText(
    "Organising 1 of 2 songs",
  );
  expect(sent).toEqual({ track_ids: ["s1", "s2"], tags: false });
  await panel.getByRole("button", { name: "Stop" }).click();
  await expect(panel.getByRole("alert")).toContainText(
    "songs already done can be undone",
  );
  await expect(panel.getByRole("status")).toHaveCount(0);
  expect(polls).toBeGreaterThan(1);
});

// #119: with tags, Review lists only songs whose tags differ (or that are
// renamed), and the Undo list shows the newest 50 at a time.
test("organising lists only songs that need a change and pages Undo", async ({
  page,
}) => {
  const row = (id: string, rename: boolean, tags_differ: boolean | null) => ({
    id,
    title: "Song " + id,
    artist: "A",
    from: `A/X/${id}.mp3`,
    to: rename ? `A/X/01 ${id}.mp3` : `A/X/${id}.mp3`,
    rename,
    tag_supported: true,
    conflict: false,
    tags_differ,
  });
  const reviews: string[] = [];
  const pages: string[] = [];
  let sent: unknown = null;
  const undone = Array.from({ length: 120 }, (_, n) => ({
    id: "op" + n,
    title: "Done " + n,
    status: "completed",
  }));
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/library/trash": (route) => route.fulfill({ json: [] }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
      "/v1/admin/library/organisation/history": (route) => {
        const url = new URL(route.request().url());
        pages.push(url.search);
        const offset = Number(url.searchParams.get("offset"));
        const limit = Number(url.searchParams.get("limit") ?? undone.length);
        return route.fulfill({ json: undone.slice(offset, offset + limit) });
      },
      "/v1/admin/library/organisation/job": (route) =>
        route.fulfill({ json: { status: "idle" } }),
      "/v1/admin/library/organisation": (route) => {
        if (route.request().method() === "POST") {
          sent = route.request().postDataJSON();
          return route.fulfill({
            json: { status: "running", total: 2, done: 0, message: null },
          });
        }
        const tags = new URL(route.request().url()).searchParams.get("tags");
        reviews.push(tags ?? "");
        return route.fulfill({
          json: {
            items: [
              row("same", false, tags ? false : null),
              row("retag", false, tags ? true : null),
              row("rename", true, tags ? false : null),
            ],
            note: "",
          },
        });
      },
    },
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/#/admin/library");
  const panel = page.locator(".system-panel").filter({
    has: page.getByRole("heading", { name: "Organise existing music" }),
  });
  const undo = panel.getByRole("button", { name: "Undo organisation" });
  await expect(undo).toHaveCount(50);
  await expect(panel.getByText("Done 0", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "Show more" }).click();
  await expect(undo).toHaveCount(100);
  await panel.getByRole("button", { name: "Show more" }).click();
  await expect(undo).toHaveCount(120);
  await expect(panel.getByRole("button", { name: "Show more" })).toHaveCount(0);
  expect(pages).toEqual([
    "?limit=51&offset=0",
    "?limit=51&offset=50",
    "?limit=51&offset=100",
  ]);

  await panel.getByRole("button", { name: "Review library" }).click();
  await expect(
    panel.locator("strong", { hasText: "A/X/rename.mp3" }),
  ).toBeVisible();
  await expect(
    panel.locator("strong", { hasText: "A/X/retag.mp3" }),
  ).toHaveCount(0);
  await panel
    .getByLabel("Apply consistent tags from current library metadata")
    .check();
  await expect(
    panel.locator("strong", { hasText: "A/X/retag.mp3" }),
  ).toBeVisible();
  await expect(
    panel.locator("strong", { hasText: "A/X/rename.mp3" }),
  ).toBeVisible();
  await expect(
    panel.locator("strong", { hasText: "A/X/same.mp3" }),
  ).toHaveCount(0);
  expect(reviews).toEqual(["", "true"]);
  await panel.getByRole("button", { name: "Select eligible changes" }).click();
  await panel
    .getByRole("button", { name: "Apply reviewed changes · 2" })
    .click();
  await expect
    .poll(() => sent)
    .toEqual({
      track_ids: ["retag", "rename"],
      tags: true,
    });
});

// #97: the whole household library loads only for the Admin pages that show it.
test("Admin reads the server library only where it is shown", async ({
  page,
}) => {
  let reads = 0;
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/queue": (route) => route.fulfill({ json: queue }),
      "/v1/admin/library": (route) => {
        reads += 1;
        return route.fulfill({ status: 503, json: { detail: "x" } });
      },
    },
  });
  await page.goto("/#/admin/downloads");
  await expect(page.getByRole("link", { name: "Queue" })).toBeVisible();
  await page.waitForTimeout(500);
  expect(reads).toBe(0);
  await page.goto("/#/admin/integrations");
  await page.waitForTimeout(500);
  expect(reads).toBe(0);
  await page.goto("/#/admin");
  await expect.poll(() => reads).toBe(1);
});

test("the owner reviews Trash and deletes exactly the listed songs for good", async ({
  page,
}) => {
  const plan = {
    days: 45,
    items: [
      {
        id: "r1",
        title: "Dreams",
        artist: "Fleetwood Mac",
        album: "Rumours",
        removed_at: 1780000000,
        size_bytes: 8_400_000,
      },
    ],
    total_bytes: 8_400_000,
    fingerprint: "abc",
  };
  let reviewed = "";
  let purged: unknown = null;
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/library/trash": (route) =>
        route.fulfill({
          json: purged
            ? []
            : [
                {
                  id: "r1",
                  track: { title: "Dreams", artist: "Fleetwood Mac" },
                  status: "removed",
                },
              ],
        }),
      "/v1/admin/library/trash/purge": (route) => {
        if (route.request().method() === "POST") {
          purged = route.request().postDataJSON();
          return route.fulfill({ json: { purged: 1, kept: 0 } });
        }
        reviewed = new URL(route.request().url()).searchParams.get("days")!;
        return route.fulfill({ json: plan });
      },
      "/v1/admin/library/organisation/history": (route) =>
        route.fulfill({ json: [] }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
    },
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/#/admin/library");
  const panel = page
    .locator(".system-panel")
    .filter({ has: page.getByRole("heading", { name: "Removed music" }) });
  await panel.getByLabel("In Trash longer than (days)").fill("45");
  await panel.getByRole("button", { name: "Review" }).click();
  await expect(panel).toContainText("Fleetwood Mac · Rumours · removed");
  expect(reviewed).toBe("45");
  await panel.getByRole("button", { name: "Delete 1 for good · 8 MB" }).click();
  await expect(panel).toContainText("No removed music.");
  expect(purged).toEqual({ days: 45, fingerprint: "abc" });
});

test("a song whose Trash deletion stopped part-way shows it needs review", async ({
  page,
}) => {
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/library/trash": (route) =>
        route.fulfill({
          json: [
            {
              id: "r1",
              track: { title: "Dreams", artist: "Fleetwood Mac" },
              status: "removed",
              needs_review: true,
            },
          ],
        }),
      "/v1/admin/library/organisation/history": (route) =>
        route.fulfill({ json: [] }),
      "/v1/admin/unassigned": (route) =>
        route.fulfill({ json: { albums: [], next_offset: null } }),
    },
  });
  await page.goto("/#/admin/library");
  const panel = page
    .locator(".system-panel")
    .filter({ has: page.getByRole("heading", { name: "Removed music" }) });
  await expect(panel).toContainText("Needs review");
  await expect(panel.getByRole("button", { name: "Restore" })).toHaveCount(0);
});

test("compared figures use the brand's rounded tabular numerals (D-84)", async ({
  page,
}) => {
  const downloading = {
    ...queue,
    items: [
      {
        ...queue.items[0],
        received_bytes: 2048 * 1024,
        total_bytes: 8192 * 1024,
      },
    ],
  };
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/admin/queue": (route) => route.fulfill({ json: downloading }),
    },
  });
  await page.goto("/#/admin");
  const figure = page
    .getByRole("list", { name: "At a glance" })
    .locator("strong")
    .first();
  await expect(figure).toHaveClass(/\bpig-amount\b/);
  expect(
    await figure.evaluate((el) => getComputedStyle(el).fontVariantNumeric),
  ).toBe("tabular-nums");
  await page.goto("/#/admin/downloads");
  await expect(page.getByText("2048 KB / 8192 KB")).toHaveClass(
    /\bpig-amount\b/,
  );
});
