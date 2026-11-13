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

test("#181: Home mixes say they are loading, not preparing, until the feed answers", async ({
  page,
}) => {
  const feed = held();
  await mockApi(page, {
    answers: {
      "/v1/home/mixes": feed.answer({
        mixes: [],
        radio: null,
        preparing: false,
      }),
    },
  });
  await page.goto("/#/home");
  const section = page.getByRole("region", { name: "Your mixes" });
  await expect(section.getByRole("status")).toHaveText("Loading your mixes…");
  feed.release();
  await expect(section.getByRole("status")).toContainText(
    "Your mixes appear as your library grows",
  );
});

test("#181: Your devices shows Loading until the sessions answer", async ({
  page,
}) => {
  const sessions = held();
  await mockApi(page, {
    answers: { "/v1/me/sessions": sessions.answer({ sessions: [] }) },
  });
  await page.goto("/#/account");
  await expect(page.getByText("Loading your devices…")).toBeVisible();
  sessions.release();
  await expect(page.getByText("Loading your devices…")).toBeHidden();
});
