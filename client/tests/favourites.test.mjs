// Codex (#248): the favourite stars belong to the signed-in account; a
// sign-in clears the last account's and a late answer for it is dropped.
import assert from "node:assert/strict";
import test from "node:test";

const target = new EventTarget();
globalThis.window = {
  addEventListener: (type, fn) => target.addEventListener(type, fn),
  dispatchEvent: (event) => target.dispatchEvent(event),
};
const { clearSessionReads } = await import("../src/api.ts");
const { currentFavourites, isFavourite, refreshFavourites, watchFavourites } =
  await import("../src/favourites.ts");

const list = (title) => ({
  id: "pig-favourites:u",
  entries: [{ id: "t-" + title, title, artist: "A" }],
});
const song = (title) => ({ key: title, title, artist: "A", state: "saved" });

test("signing in as someone else clears the stars, even from a late answer", async () => {
  watchFavourites();
  let answer = list("Mine");
  let hold;
  globalThis.fetch = async (path) => {
    if (path === "/api/v1/playlists")
      return Response.json({ playlists: [{ id: "pig-favourites:u" }] });
    const reply = answer;
    if (hold) await hold;
    return Response.json(reply);
  };
  await refreshFavourites();
  assert.ok(isFavourite(song("Mine"), currentFavourites()));

  // A read for the first account is still on its way when it signs out.
  let release;
  hold = new Promise((r) => (release = r));
  const late = refreshFavourites();
  globalThis.navigator ??= {};
  clearSessionReads();
  assert.equal(currentFavourites(), null);
  answer = list("Theirs");
  release();
  await late;
  // The first account's late answer is dropped, not shown to the next.
  assert.equal(currentFavourites(), null);
  hold = undefined;
  await refreshFavourites();
  assert.ok(!isFavourite(song("Mine"), currentFavourites()));
  assert.ok(isFavourite(song("Theirs"), currentFavourites()));
});
