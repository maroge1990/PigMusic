import { expect, test, type Page, type Route } from "@playwright/test";
import { mockApi } from "./mock";

// #239: a server fault and an unconfirmed write each say what happened.
type Devices = { phone: boolean; deletes: number };
async function devicesPage(
  page: Page,
  signOut: (route: Route, state: Devices) => unknown,
) {
  const now = Date.now() / 1000;
  const state: Devices = { phone: true, deletes: 0 };
  const session = (id: string, name: string, current = false) => ({
    id,
    kind: current ? "web" : "device",
    device_name: name,
    platform: current ? "web" : "ios",
    created_at: now - 9000,
    last_seen_at: now - 60,
    expires_at: now + 9000,
    revoked_at: null,
    current,
  });
  await mockApi(page, {
    answers: {
      "/v1/me/sessions": (route) =>
        route.fulfill({
          json: {
            sessions: [
              session("web-1", "Safari on Mac", true),
              ...(state.phone ? [session("phone-1", "iPhone")] : []),
            ],
          },
        }),
      "/v1/me/sessions/phone-1": async (route) => {
        state.deletes++;
        await signOut(route, state);
      },
      "/v1/quality": (route) =>
        route.fulfill({
          json: { preferences: [], options: [], household_default: "" },
        }),
    },
  });
  await page.goto("/#/account");
  await expect(page.locator(".session-list li")).toHaveCount(2);
  return state;
}

test("a write the server failed on shows the reference to quote", async ({
  page,
}) => {
  const state = await devicesPage(page, (route) =>
    route.fulfill({
      status: 500,
      json: {
        code: "server_error",
        detail: "Something went wrong on the server.",
        request_id: "0123abcd4567ef89",
      },
    }),
  );
  await page.getByRole("button", { name: "Sign out iPhone" }).click();
  await expect(
    page.getByText(
      "That didn't work: something went wrong on the server. Reference 0123abcd",
    ),
  ).toBeVisible();
  expect(state.deletes).toBe(1);
});

test("a write the server was too busy to confirm says so and shows what was saved", async ({
  page,
}) => {
  await devicesPage(page, (route, state) => {
    // The sign-out was in fact saved before the server ran out of time.
    state.phone = false;
    return route.fulfill({
      status: 503,
      json: {
        code: "busy_unconfirmed",
        detail:
          "The server was busy. Check whether this was saved before trying again.",
      },
    });
  });
  await page.getByRole("button", { name: "Sign out iPhone" }).click();
  await expect(
    page.getByText(
      "The server was busy, so this may or may not have been saved. Check before trying again.",
    ),
  ).toBeVisible();
  // The list is read again, so the person sees the device is gone.
  await expect(page.locator(".session-list li")).toHaveCount(1);
});
