import { expect, test, type Route } from "@playwright/test";
import { mockApi } from "./mock";

/** Holds an answer until the test lets it go. */
function held() {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => (release = resolve));
  return {
    release,
    answer: (body: unknown) => async (route: Route) => {
      await gate;
      await route.fulfill({ json: body });
    },
  };
}

// #181, D-120: while Home's shelves load there is one quiet placeholder row
// and no words; a shelf with nothing in it doesn't appear at all.
test("Home shows one placeholder row while loading and hides empty shelves", async ({
  page,
}) => {
  const mixes = held();
  const recommended = held();
  const popular = held();
  const empty = { state: "ready", artists: [], albums: [], songs: [] };
  await mockApi(page, {
    answers: {
      "/v1/home/mixes": mixes.answer({
        mixes: [],
        radio: null,
        preparing: false,
      }),
      "/v1/home/recommended": recommended.answer({
        ...empty,
        state: "no_seeds",
      }),
      "/v1/home/popular": popular.answer({
        ...empty,
        title: "Popular in Australia",
        source: "Apple Music · Australia",
        songs: [
          {
            id: "s1",
            name: "Song one",
            artist: "Someone",
            artist_id: "a1",
            kind: "song",
          },
        ],
      }),
    },
  });
  await page.goto("/#/home");
  const placeholders = page.locator(".shelf-placeholder");
  await expect(placeholders).toHaveCount(1);
  await expect(
    page.getByRole("region", { name: "Mixes, loading" }),
  ).toHaveAttribute("aria-busy", "true");
  const main = page.locator(".home-page");
  await expect(main).not.toContainText("Loading");
  await expect(main).not.toContainText("Finding");
  // No mixes: the placeholder moves on to the next shelf still loading.
  mixes.release();
  await expect(
    page.getByRole("region", { name: "Recommended for you, loading" }),
  ).toBeVisible();
  await expect(placeholders).toHaveCount(1);
  recommended.release();
  await expect(
    page.getByRole("region", { name: "Popular in Australia, loading" }),
  ).toBeVisible();
  popular.release();
  await expect(placeholders).toHaveCount(0);
  await expect(main.locator(".section-title h2")).toHaveText([
    "Popular in Australia",
  ]);
  await expect(main).not.toContainText("Recommendations appear");
});

test("#181: Your devices shows Loading until the sessions answer", async ({
  page,
}) => {
  const sessions = held();
  await mockApi(page, {
    answers: {
      // Asked for first; unanswered, its 404 hid the Loading line at once
      // and the test passed only when it looked before that arrived.
      "/v1/quality": async (route) =>
        route.fulfill({
          json: { preferences: [], options: [], household_default: "" },
        }),
      "/v1/me/sessions": sessions.answer({ sessions: [] }),
    },
  });
  await page.goto("/#/account");
  await expect(page.getByText("Loading your devices…")).toBeVisible();
  sessions.release();
  await expect(page.getByText("Loading your devices…")).toBeHidden();
});
