import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

const songs = Array.from({ length: 13 }, (_, i) => ({
  id: `m${i + 1}`,
  title: `Meteora song ${i + 1}`,
  artist: "Linkin Park",
  track_number: i + 1,
  status: i === 0 ? "completed" : i === 1 ? "failed" : "waiting_for_sources",
  request_progress: null,
  error: i === 1 ? "no_source" : null,
  source: i === 0 ? "soulseek" : null,
  quality: i === 0 ? "FLAC" : null,
}));
const groups = {
  total: 2,
  next_offset: null,
  active: 11,
  offset: 0,
  downloads_enabled: true,
  groups: [
    {
      id: "g-meteora",
      kind: "album",
      title: "Meteora",
      artist: "Linkin Park",
      release_id: "r-meteora",
      state: "waiting",
      songs: 13,
      done: 1,
      active: 0,
      waiting: 11,
      failed: 1,
      progress: 1 / 13,
      requested_at: new Date(Date.now() - 600_000).toISOString(),
      updated_at: new Date(Date.now() - 300_000).toISOString(),
      items: songs,
    },
    {
      id: "g-numb",
      kind: "song",
      title: "Numb",
      artist: "Linkin Park",
      release_id: null,
      state: "done",
      songs: 1,
      done: 1,
      active: 0,
      waiting: 0,
      failed: 0,
      progress: 1,
      requested_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
      items: [
        {
          id: "n1",
          title: "Numb",
          status: "completed",
          request_progress: null,
          source: "youtube",
          quality: "AAC 256 kbps",
        },
      ],
    },
  ],
};

test("Downloads shows one card per request, with detail inside", async ({
  page,
}) => {
  const posted: string[] = [];
  await mockApi(page, {
    answers: {
      "/v1/requests/groups": (route) => route.fulfill({ json: groups }),
      "/v1/requests/m2/retry": (route) => {
        posted.push("retry m2");
        return route.fulfill({ json: { retried: true } });
      },
      "/v1/requests": (route) => {
        if (route.request().method() === "DELETE") {
          posted.push("clear");
          return route.fulfill({ json: { cleared: 2 } });
        }
        return route.fulfill({
          json: { items: [], next_offset: null, active: 0 },
        });
      },
    },
  });
  await page.goto("/#/downloads");
  const cards = page.locator(".request-group");
  // Thirteen songs from one album request are one card, not thirteen rows.
  await expect(cards).toHaveCount(2);
  const meteora = cards.filter({ hasText: "Meteora" });
  await expect(meteora).toContainText(
    "1 of 13 songs in your library · 11 waiting · 1 couldn't be added",
  );
  await expect(meteora).toContainText("5 min ago");
  await expect(meteora.locator("summary .cover")).toHaveCount(1);
  await expect(meteora.getByText("Soulseek")).toBeHidden();
  await meteora.locator(":scope > summary").click();
  await expect(meteora).toContainText("In your library · Soulseek · FLAC");
  await meteora
    .getByRole("button", { name: "Try again: Meteora song 2" })
    .click();
  await expect.poll(() => posted).toContain("retry m2");
  await page.getByRole("button", { name: "Clear finished" }).click();
  await expect.poll(() => posted).toContain("clear");
  await expect(page.getByRole("status").first()).toContainText(
    "Cleared 2 finished songs",
  );
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});

test("#197: a download opens to its story, fresh on every opening, failures marked", async ({
  page,
}) => {
  let asked = 0;
  await mockApi(page, {
    answers: {
      "/v1/requests/groups": (route) => route.fulfill({ json: groups }),
      "/v1/requests/m2/log": (route) => {
        asked += 1;
        return route.fulfill({
          json: {
            id: "m2",
            status: "failed",
            lines: [
              {
                text: "Searched Soulseek for MP3 320 kbps: no matching copy",
                ok: true,
                seconds: 32.5,
              },
              {
                text: "Searched Soulseek for Lossless (FLAC): 2 matching copies (first: FLAC 34 MB)",
                ok: true,
                seconds: 12,
              },
              {
                text: "Checked the lossless original: refused, Lossless download looks like lossy audio",
                ok: false,
                seconds: 0.4,
              },
              { text: "No source had a copy", ok: false },
            ],
          },
        });
      },
      "/v1/requests": (route) =>
        route.fulfill({ json: { items: [], next_offset: null, active: 0 } }),
    },
  });
  await page.goto("/#/downloads");
  const meteora = page.locator(".request-group").filter({ hasText: "Meteora" });
  await meteora.locator(":scope > summary").click();
  await page.getByLabel("Details of the download of Meteora song 2").click();
  const story = page
    .locator(".download-log")
    .filter({ hasText: "Searched Soulseek for MP3 320 kbps" });
  await expect(story.locator("li")).toHaveCount(4);
  await expect(story.locator("li").nth(1)).toContainText("FLAC 34 MB");
  await expect(story.locator("li").nth(1)).toContainText("12.0 s");
  // A refused step says so in words, not by colour alone.
  await expect(story.locator("li.download-log-failed")).toHaveCount(2);
  await expect(story.locator("li").nth(2)).toContainText("Problem:");
  // Closing and opening again asks again (Codex round 2): a retry or a
  // finished download is never shown from an old answer.
  await page.getByLabel("Details of the download of Meteora song 2").click();
  await page.getByLabel("Details of the download of Meteora song 2").click();
  await expect.poll(() => asked).toBe(2);
});

test("#197: open details follow the request as it changes", async ({
  page,
}) => {
  let status = "waiting_for_sources";
  const page1 = () => ({
    ...groups,
    groups: [
      {
        ...groups.groups[1],
        state: status === "failed" ? "failed" : "waiting",
        items: [{ ...groups.groups[1].items[0], status, updated_at: status }],
      },
    ],
  });
  await mockApi(page, {
    answers: {
      "/v1/requests/groups": (route) => route.fulfill({ json: page1() }),
      "/v1/requests/n1/log": (route) =>
        route.fulfill({
          json: {
            id: "n1",
            status,
            lines:
              status === "failed"
                ? [{ text: "No source had a copy", ok: false }]
                : [{ text: "Waited in the queue", ok: true, seconds: 1 }],
          },
        }),
      "/v1/requests": (route) =>
        route.fulfill({ json: { items: [], next_offset: null, active: 0 } }),
    },
  });
  await page.goto("/#/downloads");
  await page.locator(".request-group > summary").first().click();
  await page.getByLabel("Details of the download of Numb").click();
  const story = page.locator(".download-log");
  await expect(story).toContainText("Waited in the queue");
  status = "failed";
  // The list refreshes (as after a live update); the open details follow.
  await page.evaluate(() =>
    window.dispatchEvent(new Event("music-requests-changed")),
  );
  await expect(story).toContainText("No source had a copy");
});

test("#228: a paused request reads Paused", async ({ page }) => {
  const paused = {
    ...groups,
    total: 1,
    active: 0,
    groups: [
      {
        ...groups.groups[0],
        state: "paused",
        songs: 2,
        done: 1,
        waiting: 0,
        failed: 0,
        paused: 1,
        progress: 0.5,
        items: [songs[0], { ...songs[2], status: "paused" }],
      },
    ],
  };
  await mockApi(page, {
    answers: {
      "/v1/requests/groups": (route) => route.fulfill({ json: paused }),
      "/v1/requests": (route) =>
        route.fulfill({ json: { items: [], next_offset: null, active: 0 } }),
    },
  });
  await page.goto("/#/downloads");
  const card = page.locator(".request-group");
  await expect(card).toContainText("1 of 2 songs in your library · 1 paused");
  await card.locator(":scope > summary").click();
  await expect(card.locator("li").nth(1)).toContainText("Paused");
});

// #246: a song the server is still fetching reads "Adding", as on iPhone.
test("a song the server is fetching reads Adding", async ({ page }) => {
  const song = {
    id: "f1",
    title: "Faint",
    artist: "Linkin Park",
    status: "downloading",
    request_progress: 0.4,
    error: null,
    source: "soulseek",
    quality: null,
  };
  const album = {
    ...groups.groups[0],
    id: "g-hybrid",
    title: "Hybrid Theory",
    state: "active",
    songs: 2,
    done: 0,
    active: 2,
    waiting: 0,
    failed: 0,
    progress: 0.4,
    items: [song, { ...song, id: "f2", title: "Papercut" }],
  };
  await mockApi(page, {
    answers: {
      "/v1/requests/groups": (route) =>
        route.fulfill({
          json: { ...groups, total: 1, groups: [album] },
        }),
    },
  });
  await page.goto("/#/downloads");
  const card = page.locator(".request-group");
  await expect(card).toContainText("0 of 2 songs in your library · 2 adding");
  await expect(card.getByRole("img", { name: "Adding" }).first()).toBeVisible();
  await card.locator(":scope > summary").click();
  await expect(card).toContainText("Adding · Soulseek");
  await expect(
    card.getByRole("progressbar", { name: "Adding Faint, 40 percent" }),
  ).toBeVisible();
  await expect(card).not.toContainText("Downloading");
});

test("D-120: Downloads groups requests and shows Loading while it waits", async ({
  page,
}) => {
  let answer: () => void = () => {};
  const answered = new Promise<void>((resolve) => (answer = resolve));
  const posted: string[] = [];
  const failed = {
    ...groups.groups[1],
    id: "g-faint",
    title: "Faint",
    state: "failed",
    done: 0,
    failed: 1,
    updated_at: new Date().toISOString(),
    items: [
      {
        id: "f1",
        title: "Faint",
        status: "failed",
        request_progress: null,
        error: "no_source",
      },
    ],
  };
  const fresh = {
    ...groups.groups[1],
    id: "g-papercut",
    title: "Papercut",
    updated_at: new Date().toISOString(),
  };
  await mockApi(page, {
    answers: {
      "/v1/requests/groups": async (route) => {
        await answered;
        return route.fulfill({
          json: {
            ...groups,
            total: 4,
            groups: [...groups.groups, failed, fresh],
          },
        });
      },
      "/v1/requests/f1/retry": (route) => {
        posted.push("retry f1");
        return route.fulfill({ json: { retried: true } });
      },
    },
  });
  await page.goto("/#/downloads");
  await expect(page.getByRole("status").getByText("Loading…")).toBeVisible();
  answer();
  const main = page.locator("main");
  await expect(main.locator("h2")).toHaveText([
    "Adding1",
    "Couldn't add1",
    "Added this week1",
    "Added earlier1",
  ]);
  const adding = page.getByRole("region", { name: /^Adding/ });
  await expect(adding).toContainText("Meteora");
  const couldNot = page.getByRole("region", { name: /^Couldn't add/ });
  await expect(couldNot).toContainText("Faint");
  // Try again sits on the row itself, without opening it.
  await couldNot.getByRole("button", { name: "Try again: Faint" }).click();
  await expect.poll(() => posted).toEqual(["retry f1"]);
  await expect(
    page.getByRole("region", { name: /^Added this week/ }),
  ).toContainText("Papercut");
  await expect(
    page.getByRole("region", { name: /^Added earlier/ }),
  ).toContainText("Numb");
});
