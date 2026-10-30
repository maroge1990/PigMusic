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
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Sign out other devices" }).click();
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
  await expect
    .poll(() => requests)
    .toEqual(["POST /v1/auth/logout", "DELETE /v1/me/sessions/session-1"]);
  await expect(page.getByLabel("Username")).toHaveCount(0);
});
