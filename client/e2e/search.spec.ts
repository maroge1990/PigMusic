import { expect, test, type Route } from "@playwright/test";
import { artist, mockApi } from "./mock";

test("search types ahead without a request per keystroke", async ({ page }) => {
  const asked: string[] = [];
  await mockApi(page, {
    answers: {
      "/v1/search": (route) => {
        asked.push(
          "library:" + new URL(route.request().url()).searchParams.get("q"),
        );
        return route.fulfill({ json: { tracks: [], household_tracks: [] } });
      },
      "/v1/catalogue/artists": (route) => {
        const q = new URL(route.request().url()).searchParams.get("q") || "";
        asked.push("catalogue:" + q);
        return route.fulfill({
          json: {
            artists: [
              {
                id: "mb-charli",
                name: artist.name,
                description: "English singer",
              },
              { id: "mb-other", name: q + " Band", description: "" },
            ],
            offset: 0,
            total: 2,
            next_offset: null,
          },
        });
      },
    },
  });
  await page.goto("/#/search");
  const box = page.getByRole("searchbox", {
    name: "Search artists, albums or songs",
  });
  await box.pressSequentially("Charli", { delay: 40 });
  const discover = page.locator(".discover-list");
  await expect(discover).toContainText("Charli Band");
  // One pause, one search: nothing was sent for "C", "Ch", "Cha" …
  expect(asked).toEqual(["library:Charli", "catalogue:Charli"]);
  await expect(page).toHaveURL(/#\/search\?q=Charli$/);
  // Discover rows show artwork, type and what you already have.
  const charli = discover.getByRole("link", { name: /English singer/ });
  await expect(charli).toContainText("Artist · English singer");
  await expect(charli).toContainText("In your library");
  await expect(charli.locator(".cover")).toHaveCount(1);
  await expect(
    discover.getByRole("link", { name: /Charli Band/ }),
  ).not.toContainText("In your library");
  // Changing and going back reuses the earlier answers.
  await box.press("Backspace");
  await expect(discover).toContainText("Charl Band");
  await box.press("i");
  await expect(discover).toContainText("Charli Band");
  expect(asked).toEqual([
    "library:Charli",
    "catalogue:Charli",
    "library:Charl",
    "catalogue:Charl",
  ]);
  // One letter searches your library only.
  await box.fill("C");
  await expect(
    page.getByText("Type another letter to search the catalogue."),
  ).toBeVisible();
  expect(asked.filter((a) => a === "catalogue:C")).toEqual([]);
});

test("signing in as someone else never shows the previous person's search", async ({
  page,
}) => {
  let person: string | null = "pat";
  const asked: string[] = [];
  await mockApi(page, {
    answers: {
      // The live feed never answers, so no library revision clears anything.
      "/v1/events": () => new Promise(() => {}),
      "/v1/me": (route) =>
        person
          ? route.fulfill({
              json: {
                id: person,
                username: person,
                display_name: person,
                role: "member",
              },
            })
          : route.fulfill({
              status: 401,
              json: {
                detail: "Please sign in to continue.",
                code: "authentication_required",
              },
            }),
      "/v1/auth/logout": (route) => {
        person = null;
        return route.fulfill({ json: {} });
      },
      "/v1/auth/login": (route) => {
        person = JSON.parse(route.request().postData() || "{}").username;
        return route.fulfill({ json: {} });
      },
      "/v1/me/sessions": (route) => route.fulfill({ json: { sessions: [] } }),
      "/v1/quality": (route) =>
        route.fulfill({
          json: { preferences: [], options: [], household_default: "" },
        }),
      "/v1/search": (route) => {
        asked.push(person || "nobody");
        return route.fulfill({
          json: {
            tracks: [
              {
                id: `${person}-song`,
                title: `${person}'s saved song`,
                artist: "Charli xcx",
                album: "Brat",
                album_id: "album-1",
                duration: 180,
                server_state: "in_library",
              },
            ],
            household_tracks: [],
          },
        });
      },
      "/v1/catalogue/artists": (route) =>
        route.fulfill({
          json: { artists: [], offset: 0, total: 0, next_offset: null },
        }),
    },
  });
  await page.goto("/#/search?q=Charli");
  await expect(page.getByText("pat's saved song")).toBeVisible();
  await page.goto("/#/account");
  await page.getByRole("button", { name: "Sign out of PigMusic" }).click();
  await page.getByLabel("Username").fill("sam");
  await page.getByLabel("Password").fill("generated e2e password");
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.goto("/#/search?q=Charli");
  await expect(page.getByText("sam's saved song")).toBeVisible();
  await expect(page.getByText("pat's saved song")).toHaveCount(0);
  expect(asked).toEqual(["pat", "sam"]);
});

test("#182: the library answers 150 ms after typing; the catalogue waits for a longer pause", async ({
  page,
}) => {
  await page.clock.install();
  const asked: string[] = [];
  const record = (kind: string) => (route: Route) => {
    asked.push(
      kind + ":" + new URL(route.request().url()).searchParams.get("q"),
    );
    return route.fulfill({
      json:
        kind === "library"
          ? { tracks: [], household_tracks: [] }
          : { artists: [], offset: 0, total: 0, next_offset: null },
    });
  };
  await mockApi(page, {
    answers: {
      "/v1/search": record("library"),
      "/v1/catalogue/artists": record("catalogue"),
    },
  });
  await page.goto("/#/search");
  const box = page.getByRole("searchbox", {
    name: "Search artists, albums or songs",
  });
  await expect(box).toBeVisible();
  // Time moves only when the test says so.
  await page.clock.pauseAt(Date.now() + 60_000);
  await box.pressSequentially("Cha");
  await page.clock.runFor(160);
  await expect.poll(() => asked).toEqual(["library:Cha"]);
  // A 250 ms pause between words is not long enough to ask the catalogue.
  await page.clock.runFor(90);
  await box.pressSequentially("r");
  await page.clock.runFor(160);
  await expect.poll(() => asked).toEqual(["library:Cha", "library:Char"]);
  await page.clock.runFor(250);
  await expect
    .poll(() => asked)
    .toEqual(["library:Cha", "library:Char", "catalogue:Char"]);
});
