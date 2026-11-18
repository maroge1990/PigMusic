import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

// #250, D-79: what can't be undone is asked in the app's own dialog, with
// focus on Cancel; Escape and Cancel do nothing.

test.beforeEach(({ page }) => {
  page.on("dialog", () => {
    throw new Error("The browser's own dialog was used");
  });
});

test("Sign out everywhere else asks in the app; Cancel and Escape do nothing", async ({
  page,
}) => {
  let signedOut = 0;
  const session = (id: string, current: boolean) => ({
    id,
    device_name: current ? "Safari on Mac" : "iPhone",
    client: "web",
    created_at: 0,
    last_seen_at: 0,
    current,
    revoked_at: null,
  });
  await mockApi(page, {
    answers: {
      "/v1/me/sessions": (route) => {
        if (route.request().method() === "DELETE") {
          signedOut++;
          return route.fulfill({ json: { revoked: 1 } });
        }
        return route.fulfill({
          json: { sessions: [session("a", true), session("b", false)] },
        });
      },
      "/v1/quality": (route) =>
        route.fulfill({
          json: { preferences: [], options: [], household_default: "" },
        }),
    },
  });
  await page.goto("/#/account");
  const open = page.getByRole("button", { name: "Sign out other devices" });
  const ask = page.getByRole("dialog", { name: "Sign out everywhere else?" });
  await open.click();
  await expect(ask).toBeVisible();
  await expect(ask.getByRole("button", { name: "Cancel" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(ask).toHaveCount(0);
  await open.click();
  await ask.getByRole("button", { name: "Cancel" }).click();
  await expect(ask).toHaveCount(0);
  expect(signedOut).toBe(0);
  await open.click();
  const action = ask.getByRole("button", { name: "Sign out everywhere else" });
  // Destructive: the red button, never the brand's accent.
  expect(await action.getAttribute("class")).toBe("danger-button");
  await action.click();
  await expect.poll(() => signedOut).toBe(1);
  await expect(page.locator(".notice-area")).toContainText(
    "Signed out 1 other device.",
  );
});

test("replacing the whole-library review asks in the app first", async ({
  page,
}) => {
  const posted: string[] = [];
  const migration = {
    source_name: "Library.xml",
    imported_at: "2026-10-21T00:00:00Z",
    albums: [],
    stats: { tracks: 30, saved: 10, missing: 20, review: 0, selected: 4 },
  };
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/imports/apple/preview": (route) =>
        route.fulfill({
          json: {
            playlists: 2,
            tracks: 30,
            saved: 10,
            household: 5,
            missing: 15,
          },
        }),
      "/v1/admin/migration": (route) => route.fulfill({ json: migration }),
      "/v1/admin/migration/import": (route) => {
        posted.push(route.request().method());
        return route.fulfill({ json: migration });
      },
    },
  });
  await page.goto("/#/library/playlists");
  await page.getByLabel("Apple Music XML export").setInputFiles({
    name: "Library.xml",
    mimeType: "text/xml",
    buffer: Buffer.from('<?xml version="1.0"?><plist version="1.0"></plist>'),
  });
  await page.getByText("Admin: review the whole library").click();
  const review = page.getByRole("button", { name: "Review whole library" });
  const ask = page.getByRole("dialog", {
    name: "Replace the whole-library review?",
  });
  await review.click();
  await expect(ask.getByRole("button", { name: "Cancel" })).toBeFocused();
  await ask.getByRole("button", { name: "Cancel" }).click();
  await expect(ask).toHaveCount(0);
  expect(posted).toEqual([]);
  await review.click();
  await ask.getByRole("button", { name: "Replace review" }).click();
  await expect(page).toHaveURL(/#\/admin\/migration\?imported=1$/);
  expect(posted).toEqual(["POST"]);
});

test("a question left open when the session ends is gone for the next account (Codex round 1)", async ({
  page,
}) => {
  const deletes: string[] = [];
  let person = "pat";
  await mockApi(page, {
    answers: {
      "/v1/me": (route) =>
        route.fulfill({
          json: {
            id: person,
            username: person,
            display_name: person,
            role: "member",
            session_id: "s-" + person,
          },
        }),
      "/v1/auth/login": (route) => route.fulfill({ json: {} }),
      "/v1/me/sessions": (route) => {
        if (route.request().method() === "DELETE") {
          deletes.push(person);
          return route.fulfill({ json: { revoked: 1 } });
        }
        return route.fulfill({
          json: {
            sessions: [
              {
                id: "a",
                device_name: "Mac",
                client: "web",
                created_at: 0,
                last_seen_at: 0,
                current: true,
                revoked_at: null,
              },
              {
                id: "b",
                device_name: "iPhone",
                client: "app",
                created_at: 0,
                last_seen_at: 0,
                current: false,
                revoked_at: null,
              },
            ],
          },
        });
      },
      "/v1/quality": (route) =>
        route.fulfill({
          json: { preferences: [], options: [], household_default: "" },
        }),
    },
  });
  await page.goto("/#/account");
  await page.getByRole("button", { name: "Sign out other devices" }).click();
  const ask = page.getByRole("dialog", { name: "Sign out everywhere else?" });
  await expect(ask).toBeVisible();
  // Pat's session is revoked elsewhere while the question is open.
  await page.evaluate(() =>
    window.dispatchEvent(new Event("music-auth-required")),
  );
  await expect(page.getByLabel("Username")).toBeVisible();
  person = "sam";
  await page.getByLabel("Username").fill("sam");
  await page.getByLabel("Password").fill("a password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByLabel("Username")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Sign out other devices" }),
  ).toBeVisible();
  await expect(ask).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(deletes).toEqual([]);
});
