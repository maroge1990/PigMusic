import { expect, test } from "@playwright/test";
import { mockApi } from "./mock";

test("Your devices names each session and can sign out the others", async ({
  page,
}) => {
  const now = Date.now() / 1000;
  let signedOut = false;
  const sessions = () => [
    {
      id: "web-1",
      kind: "web",
      device_name: "Safari on Mac",
      platform: "web",
      created_at: now - 9000,
      last_seen_at: now - 60,
      expires_at: now + 9000,
      revoked_at: null,
      current: true,
    },
    ...(signedOut
      ? []
      : [
          {
            id: "phone-1",
            kind: "device",
            device_name: "iPhone",
            platform: "ios",
            created_at: now - 90000,
            last_seen_at: now - 7200,
            expires_at: now + 90000,
            revoked_at: null,
          },
        ]),
  ];
  await mockApi(page, {
    answers: {
      "/v1/me/sessions": (route) => {
        if (route.request().method() === "DELETE") {
          signedOut = true;
          return route.fulfill({ json: { revoked: 1 } });
        }
        return route.fulfill({ json: { sessions: sessions() } });
      },
      "/v1/quality": (route) =>
        route.fulfill({
          json: { preferences: [], options: [], household_default: "" },
        }),
    },
  });
  await page.goto("/#/account");
  const devices = page.locator(".session-list li");
  await expect(devices).toHaveCount(2);
  await expect(devices.first()).toContainText("Safari on Mac · This device");
  await expect(devices.first()).toContainText("Web · In use now");
  await expect(devices.first().getByRole("button")).toHaveCount(0);
  await expect(devices.nth(1)).toContainText(
    "iPhone or iPad app · Last used 2 h ago",
  );
  await page.getByRole("button", { name: "Sign out other devices" }).click();
  await page
    .getByRole("dialog", { name: "Sign out everywhere else?" })
    .getByRole("button", { name: "Sign out everywhere else" })
    .click();
  await expect(page.getByText("Signed out 1 other device.")).toBeVisible();
  await expect(devices).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Sign out other devices" }),
  ).toHaveCount(0);
});

test("signing out with the server unreachable clears this device and finishes later without touching a newer sign-in (#80)", async ({
  context,
}) => {
  let online = false;
  let person: string | null = "pat";
  const requests: string[] = [];
  const answers = {
    "/v1/me": (route: import("@playwright/test").Route) =>
      person
        ? route.fulfill({
            json: {
              id: person,
              username: person,
              display_name: person,
              role: "member",
              session_id: "session-1",
            },
          })
        : route.fulfill({
            status: 401,
            json: {
              detail: "Please sign in to continue.",
              code: "authentication_required",
            },
          }),
    "/v1/auth/logout": (route: import("@playwright/test").Route) => {
      requests.push("POST /v1/auth/logout");
      return online ? route.fulfill({ json: {} }) : route.abort("failed");
    },
    "/v1/me/sessions/session-1": (route: import("@playwright/test").Route) => {
      requests.push(`${route.request().method()} /v1/me/sessions/session-1`);
      return online
        ? route.fulfill({ json: { revoked: true } })
        : route.abort("failed");
    },
  };
  await mockApi(context, { answers });
  const page = await context.newPage();
  await page.goto("/#/account");
  await page.getByRole("button", { name: "Sign out of PigMusic" }).click();
  // Cleared at once, and told why the server still has to finish it.
  await expect(page.getByText(/Signed out on this device/)).toBeVisible();
  await expect(page.getByLabel("Username")).toBeVisible();
  // Another tab signs in again before the server is back (a newer session).
  online = true;
  person = "pat";
  const other = await context.newPage();
  await other.goto("/#/home");
  // The first tab reopens: it ends only the old session, by its id.
  await page.reload();
  // Either tab may finish it (ending an ended session is harmless), but only
  // ever by that session's id, never through logout and its cookie.
  await expect.poll(() => requests.length).toBeGreaterThan(1);
  expect(requests[0]).toBe("POST /v1/auth/logout");
  expect(new Set(requests.slice(1))).toEqual(
    new Set(["DELETE /v1/me/sessions/session-1"]),
  );
  await expect(page.getByLabel("Username")).toHaveCount(0);
});

test("D-120: Account is a profile card beside sections with icons", async ({
  page,
}) => {
  const saved: unknown[] = [];
  await mockApi(page, {
    role: "owner",
    answers: {
      "/v1/me/sessions": (route) =>
        route.fulfill({
          json: {
            sessions: [
              {
                id: "s1",
                device_name: "Safari on Mac",
                platform: "web",
                last_seen_at: Date.now() / 1000,
                revoked_at: null,
                current: true,
              },
              {
                id: "s2",
                device_name: "Pat's iPhone",
                platform: "ios",
                last_seen_at: Date.now() / 1000 - 7200,
                revoked_at: null,
              },
            ],
          },
        }),
      "/v1/preferences": (route) => {
        if (route.request().method() === "PUT")
          saved.push(route.request().postDataJSON().appearance);
        return route.fulfill({
          json: { appearance: "system", default_tab: "artists" },
        });
      },
    },
  });
  await page.goto("/#/account");
  const profile = page.locator(".account-profile");
  await expect(profile.locator(".account-avatar")).toHaveText("P");
  await expect(profile.getByRole("heading", { name: "Pat" })).toBeVisible();
  await expect(profile).toContainText("2 songs in your library");
  await expect(
    profile.getByRole("button", { name: "Sign out of PigMusic" }),
  ).toBeVisible();
  // The sections in the drawn order, each with its icon; Admin last.
  await expect(page.locator(".account-grid h2")).toHaveText([
    "Preferences",
    "Your music quality",
    "Password",
    "Devices and sessions",
  ]);
  for (const h2 of await page.locator(".account-grid h2").all())
    await expect(h2.locator("svg")).toHaveCount(1);
  await expect(page.locator("main > section").last()).toHaveAttribute(
    "aria-label",
    "Admin shortcuts",
  );
  // Appearance is three choices; one tap saves it.
  const appearance = page.getByRole("group", { name: "Appearance" });
  await expect(appearance.getByRole("radio")).toHaveCount(3);
  await appearance.getByText("Dark").click();
  await expect.poll(() => saved).toEqual(["dark"]);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  // The password form opens from its button.
  await expect(page.getByLabel("Current password")).toHaveCount(0);
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByLabel("Current password")).toBeFocused();
  // No explanatory paragraphs (D-101).
  await expect(page.getByText("Shared files retain")).toHaveCount(0);
});
