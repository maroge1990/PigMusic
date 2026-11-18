import { expect, test, type Page, type Route } from "@playwright/test";
import { mockApi } from "./mock";

// Codex round 3 (#250): writes the server takes before sign-in name no
// session, so activating an invite or a password reset works signed out.

async function signedOut(page: Page) {
  const setups: unknown[] = [];
  let signedIn = false;
  await mockApi(page, {
    answers: {
      "/v1/me": (route: Route) =>
        signedIn
          ? route.fulfill({
              json: {
                id: "u2",
                username: "sam",
                display_name: "Sam",
                role: "member",
                session_id: "session-2",
              },
            })
          : route.fulfill({
              status: 401,
              json: {
                detail: "Please sign in to continue.",
                code: "authentication_required",
              },
            }),
      "/v1/setup": (route: Route) => {
        setups.push(route.request().postDataJSON());
        return route.fulfill({ json: { user: { username: "sam" } } });
      },
      "/v1/auth/login": (route: Route) => {
        signedIn = true;
        return route.fulfill({ json: {} });
      },
    },
  });
  return setups;
}

async function choosePassword(page: Page) {
  await page
    .getByLabel("Password", { exact: true })
    .fill("a long new password");
  await page.getByLabel("Confirm password").fill("a long new password");
  await page.getByRole("button", { name: "Create account" }).click();
}

test("an invite link is activated signed out", async ({ page }) => {
  const setups = await signedOut(page);
  await page.goto("/#/join?code=INVITE-1");
  await choosePassword(page);
  await expect
    .poll(() => setups)
    .toEqual([{ code: "INVITE-1", password: "a long new password" }]);
  await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Couldn't tell who is signed in/)).toHaveCount(0);
});

test("a password reset code is used signed out", async ({ page }) => {
  const setups = await signedOut(page);
  await page.goto("/#/setup");
  await page.getByLabel("Setup code").fill("RESET-1");
  await choosePassword(page);
  await expect
    .poll(() => setups)
    .toEqual([{ code: "RESET-1", password: "a long new password" }]);
  await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);
});
