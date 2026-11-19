import {
  expect,
  test,
  type BrowserContext,
  type Route,
} from "@playwright/test";
import { album, mockApi } from "./mock";

const song = (i: number) => ({
  ...album.tracks[0],
  id: "lib-" + i,
  title: "Song " + i,
});
/** The library's songs as the server pages them: 200 a page. */
const page = (offset: number, total: number) => {
  const end = Math.min(total, offset + 200);
  return {
    items: Array.from({ length: end - offset }, (_, i) => song(offset + i)),
    total,
    offset,
    next_offset: end < total ? end : null,
  };
};

async function silentPlayback(context: BrowserContext) {
  await context.route("**/api/v1/playback/*/url", (route) =>
    route.fulfill({
      json: { url: "/api/v1/playback/files/silence", expires_in: 60 },
    }),
  );
  await context.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
}

// Codex (#251): Play read only the first 200 songs; it now reads each next
// page before the queue runs out, so playback carries on to song 201.
test("Library Play carries on past the first page of songs", async ({
  context,
}) => {
  test.setTimeout(90_000);
  const offsets: string[] = [];
  await mockApi(context, {
    answers: {
      "/v1/library-pages/tracks": (route: Route) => {
        const offset = Number(
          new URL(route.request().url()).searchParams.get("offset"),
        );
        offsets.push(String(offset));
        return route.fulfill({ json: page(offset, 201) });
      },
    },
  });
  await silentPlayback(context);
  const tab = await context.newPage();
  await tab.goto("/#/library/albums");
  await tab
    .locator(".library-toolbar")
    .getByRole("button", { name: "Play" })
    .click();
  const mini = tab.locator(".mini-player");
  await expect(mini).toContainText("Song 0");
  expect(offsets).toEqual(["0"]);
  const next = mini.getByRole("button", { name: "Next song" });
  for (let i = 1; i <= 200; i++) {
    await next.click();
    await expect(mini.locator("strong").first()).toHaveText("Song " + i);
  }
  expect(offsets).toEqual(["0", "200"]);
});

// Codex (#251): a Library Play whose songs arrive after the session ended
// (here another tab signs in as someone else) plays nothing.
test("a Library Play answered after another sign-in plays nothing", async ({
  context,
}) => {
  let who = "u1";
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  const readsAs: string[] = [];
  await mockApi(context, {
    answers: {
      "/v1/me": (route: Route) =>
        readsAs.push(who) &&
        route.fulfill({
          json: {
            id: who,
            username: who,
            display_name: who === "u1" ? "Pat" : "Sam",
            role: "member",
            session_id: who === "u1" ? "session-1" : "session-2",
          },
        }),
      "/v1/library-pages/tracks": async (route: Route) => {
        await held;
        return route.fulfill({ json: page(0, 3) });
      },
    },
  });
  await silentPlayback(context);
  const tab = await context.newPage();
  await tab.goto("/#/library/albums");
  await tab
    .locator(".library-toolbar")
    .getByRole("button", { name: "Play" })
    .click();

  who = "u2";
  const other = await context.newPage();
  await other.goto("/#/home");
  await other.evaluate(() => {
    const channel = new BroadcastChannel("pig-music-session");
    channel.postMessage({ change: "signed-in", tab: "other-tab", at: 1 });
    channel.close();
  });
  // This tab has read who is signed in now.
  await expect
    .poll(() => readsAs.filter((id) => id === "u2").length)
    .toBeGreaterThanOrEqual(2);
  release();
  await tab.waitForTimeout(500);
  await expect(tab.locator(".mini-player")).toBeHidden();
  await expect(tab.getByText("Song 0")).toHaveCount(0);
});
