import { expect, test } from "@playwright/test";
import { album, mockApi } from "./mock";

// #97: the seek slider moves in useful steps and says where it is.
test("the seek slider steps five seconds and reads as time", async ({
  page,
}) => {
  await mockApi(page, {
    answers: {
      "/v1/playback/t1/url": (route) =>
        route.fulfill({
          json: { url: "/api/v1/playback/files/held", expires_in: 60 },
        }),
      // The audio never arrives, so only the slider moves the position.
      "/v1/playback/files/held": () => new Promise(() => {}),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page.getByText("Von dutch").first().click();
  await page
    .getByRole("button", { name: "Open now playing: Von dutch" })
    .click();
  const slider = page.getByRole("slider", { name: "Playback position" });
  await expect(slider).toHaveAttribute("aria-valuetext", "0:00 of 3:00");
  await slider.focus();
  await page.keyboard.press("ArrowRight");
  await expect(slider).toHaveAttribute("aria-valuetext", "0:05 of 3:00");
  await page.keyboard.press("ArrowLeft");
  await expect(slider).toHaveAttribute("aria-valuetext", "0:00 of 3:00");
});

// #97: a song's menu takes focus and the arrow keys move through it.
test("the song menu moves with the arrow keys", async ({ page }) => {
  await mockApi(page, {
    answers: {
      "/v1/favourites": (route) =>
        route.fulfill({ json: { favourited: false } }),
    },
  });
  await page.goto(`/#/library/album/${album.id}`);
  await page
    .getByRole("button", { name: "More options for Von dutch" })
    .click();
  const menu = page.getByRole("menu", { name: "Von dutch" });
  await expect(menu.getByRole("menuitem", { name: "Play next" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(
    menu.getByRole("menuitem", { name: "Add to queue" }),
  ).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("ArrowUp");
  const items = menu.getByRole("menuitem");
  await expect(items.last()).toBeFocused();
  await page.keyboard.press("Home");
  await expect(items.first()).toBeFocused();
});

// #97: a card button holds phrasing content only (no <p> inside a button).
test("Home cards hold no paragraphs inside their buttons", async ({ page }) => {
  const item = (id: string, name: string) => ({
    id,
    name,
    artist: "Someone",
    artist_id: "a-" + id,
  });
  const feed = {
    state: "ready",
    title: "",
    artists: [item("ar1", "Artist one")],
    albums: [item("al1", "Album one")],
    songs: [],
  };
  await mockApi(page, {
    answers: {
      "/v1/home/recommended": (route) => route.fulfill({ json: feed }),
      "/v1/home/popular": (route) => route.fulfill({ json: feed }),
    },
  });
  await page.goto("/#/home");
  await expect(page.locator(".home-grid button").first()).toBeVisible();
  await expect(page.locator("button p")).toHaveCount(0);
  await expect(
    page.locator(".home-grid button", { hasText: "Album one" }).first(),
  ).toContainText("Someone");
});

// #97: the library artist page's album cards are buttons too, so they hold
// no heading or paragraph; each still reads as its album.
test("library artist album cards hold no headings or paragraphs", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/#/library/artist/artist-1");
  const card = page.locator(".personal-grid button", { hasText: "Brat" });
  await expect(card).toBeVisible();
  await expect(page.locator("button h3, button p")).toHaveCount(0);
  await expect(card).toHaveAccessibleName(/Brat.*2 songs/);
  await card.click();
  await expect(page).toHaveURL(/#\/library\/album\/album-1$/);
});

// #97: "You do not have access" belongs to the page it happened on.
test("the access-denied notice clears on the next page", async ({ page }) => {
  await mockApi(page);
  await page.goto("/#/home");
  await expect(page.getByRole("heading", { name: "Home" })).toBeVisible();
  await page.evaluate(() =>
    window.dispatchEvent(new CustomEvent("music-forbidden")),
  );
  const notice = page.getByText("You do not have access to that action.");
  await expect(notice).toBeVisible();
  await page.goto("/#/library/albums");
  await expect(page.getByText("Pat · 2 songs")).toBeVisible();
  await expect(notice).toHaveCount(0);
});

// #181: every control named in the audit is at least 44 px tall, and a
// focus ring inside a sideways shelf is drawn inside the control.
test("small controls meet the 44 px target; shelf focus rings sit inside", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/#/home");
  await expect(page.locator("main")).toBeVisible();
  const sizes = await page.evaluate(() => {
    const host = document.createElement("div");
    host.innerHTML = `
      <div class="tab-list"><button id="tab">Albums</button></div>
      <div class="popular-pager"><button class="icon-button" id="pager">›</button></div>
      <div class="np-queue"><span class="np-queue-tools"><button id="queue">↑</button></span></div>`;
    document.querySelector("main")!.append(host);
    return ["tab", "pager", "queue"].map((id) => {
      const box = document.getElementById(id)!.getBoundingClientRect();
      return [id, Math.round(Math.min(box.width, box.height))];
    });
  });
  for (const [id, size] of sizes)
    expect(size, `${id} is ${size}px`).toBeGreaterThanOrEqual(44);

  const offsets = await page.evaluate(() => {
    const host = document.createElement("div");
    host.innerHTML = `
      <div class="home-songs"><button id="shelf">Song</button></div>
      <div class="mix-row"><button id="mix">Mix</button></div>`;
    document.querySelector("main")!.append(host);
    return ["shelf", "mix"].map((id) => {
      const button = document.getElementById(id)!;
      button.focus({ focusVisible: true } as FocusOptions);
      return [
        id,
        button.matches(":focus-visible"),
        getComputedStyle(button).outlineOffset,
      ];
    });
  });
  for (const [id, visible, offset] of offsets)
    if (visible) expect(offset, `${id} ring`).toBe("-2px");
  expect(offsets.some(([, visible]) => visible)).toBe(true);
});
