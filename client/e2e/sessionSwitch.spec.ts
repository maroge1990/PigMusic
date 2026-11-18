import { expect, test, type Route } from "@playwright/test";
import { album, mockApi } from "./mock";

// Codex final round: another tab signing in as someone else ends this tab's
// session exactly as signing out does: playback stops, the queue goes, and
// the private page open is not kept, before the new account is read.
test("another tab's sign-in ends playback and the open playlist here", async ({
  context,
}) => {
  let who = "u1";
  const playlistReads: string[] = [];
  const answers = {
    "/v1/me": (route: Route) =>
      route.fulfill({
        json: {
          id: who,
          username: who === "u1" ? "pat" : "sam",
          display_name: who === "u1" ? "Pat" : "Sam",
          role: "member",
          session_id: who === "u1" ? "session-1" : "session-2",
        },
      }),
    "/v1/playlists/p1": (route: Route) => {
      playlistReads.push(who);
      return who === "u1"
        ? route.fulfill({
            json: {
              id: "p1",
              name: "Pat's secret mix",
              revision: 1,
              entries: album.tracks.map((t, position) => ({
                ...t,
                saved_track: t,
                entry_id: "e" + position,
                position,
              })),
            },
          })
        : route.fulfill({
            status: 404,
            json: { detail: "Playlist not found", code: "not_found" },
          });
    },
  };
  await mockApi(context, { answers });
  await context.route("**/api/v1/playback/*/url", (route) =>
    route.fulfill({
      json: { url: "/api/v1/playback/files/silence", expires_in: 60 },
    }),
  );
  await context.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.resolve();
  });
  const page = await context.newPage();
  await page.goto("/#/library/playlist/p1");
  await expect(
    page.getByRole("heading", { name: "Pat's secret mix" }),
  ).toBeVisible();
  await page.locator(".song-row", { hasText: "Von dutch" }).click();
  const mini = page.locator(".mini-player");
  await expect(mini).toContainText("Von dutch");
  await expect
    .poll(() => page.evaluate(() => !!localStorage.getItem("pig-music-queue")))
    .toBe(true);

  // Another tab signs in as Sam.
  who = "u2";
  const other = await context.newPage();
  await other.goto("/#/home");
  await other.evaluate(() => {
    const channel = new BroadcastChannel("pig-music-session");
    channel.postMessage({ change: "signed-in", tab: "other-tab", at: 1 });
    channel.close();
  });

  await expect(mini).toBeHidden();
  await expect(
    page.getByRole("heading", { name: "Pat's secret mix" }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(() => localStorage.getItem("pig-music-queue")),
  ).toBeNull();
  // Sam is signed in here now, and Pat's playlist is not shown to him.
  await expect.poll(() => playlistReads.at(-1)).toBe("u2");
  await expect(page.getByText("Pat's secret mix")).toHaveCount(0);
});
