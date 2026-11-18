// #248, #249 (D-118): one song menu and one collection menu, in one order.
import assert from "node:assert/strict";
import test from "node:test";
import {
  collectionMenu,
  collectionMenuLabels,
  songMenu,
  songMenuLabels,
  tapQueue,
} from "../src/menus.ts";

const words = (groups) =>
  groups.map((group) => group.map((item) => songMenuLabels[item]));
const yours = {
  state: "saved",
  hasFile: true,
  album: true,
  artist: true,
};

test("a library song: queue, favourite and playlist, Download, Go to, Remove", () => {
  assert.deepEqual(words(songMenu({ ...yours, page: "library" })), [
    ["Play next", "Add to queue"],
    ["Favourite", "Add to playlist"],
    ["Download"],
    ["Go to album", "Go to artist"],
    ["Remove from library"],
  ]);
});

test("a song not yours offers Add to library first, and no removal", () => {
  assert.deepEqual(
    words(
      songMenu({
        page: "search",
        state: "missing",
        hasFile: false,
        album: true,
        artist: true,
      }),
    ),
    [
      ["Play next", "Add to queue"],
      ["Add to library", "Favourite", "Add to playlist"],
      ["Go to album", "Go to artist"],
    ],
  );
  // On the server but not yours: it can be added and saved to this computer.
  assert.deepEqual(
    words(songMenu({ ...yours, state: "server", page: "search" })),
    [
      ["Play next", "Add to queue"],
      ["Add to library", "Favourite", "Add to playlist"],
      ["Download"],
      ["Go to album", "Go to artist"],
    ],
  );
});

test("a song being added shows neither Add to library nor Remove", () => {
  const groups = songMenu({ ...yours, state: "pending", page: "library" });
  assert.ok(!groups.flat().includes("add-to-library"));
  assert.ok(!groups.flat().includes("remove-from-library"));
});

test("a playlist adds its own items before Remove from library", () => {
  assert.deepEqual(
    words(songMenu({ ...yours, page: "playlist", editablePlaylist: true })),
    [
      ["Play next", "Add to queue"],
      ["Favourite", "Add to playlist"],
      ["Download"],
      ["Go to album", "Go to artist"],
      ["Move up", "Remove from playlist"],
      ["Remove from library"],
    ],
  );
  // Favourites is a playlist nobody reorders.
  assert.ok(
    !songMenu({ ...yours, page: "playlist" })
      .flat()
      .includes("remove-from-playlist"),
  );
});

test("Up next adds Remove from Up next", () => {
  assert.deepEqual(words(songMenu({ ...yours, page: "queue" })), [
    ["Play next", "Add to queue"],
    ["Favourite", "Add to playlist"],
    ["Download"],
    ["Go to album", "Go to artist"],
    ["Remove from Up next"],
    ["Remove from library"],
  ]);
});

test("on its own album or artist page a song has no Go to for it", () => {
  assert.deepEqual(
    songMenu({ ...yours, page: "library", album: false, artist: false }),
    [
      ["play-next", "add-to-queue"],
      ["favourite", "add-to-playlist"],
      ["download"],
      ["remove-from-library"],
    ],
  );
});

test("albums, artists and playlists share one collection menu", () => {
  for (const kind of ["album", "artist", "playlist"])
    assert.deepEqual(
      collectionMenu(kind).map((g) => g.map((i) => collectionMenuLabels[i])),
      [
        ["Play", "Shuffle", "Play next"],
        ["Add to library", "Add to playlist", "Download"],
      ],
      kind,
    );
});

test("only a mix adds Save and Hide this edition", () => {
  assert.deepEqual(
    collectionMenu("mix").map((g) => g.map((i) => collectionMenuLabels[i])),
    [
      ["Play", "Shuffle", "Play next"],
      ["Add to library", "Add to playlist", "Download"],
      ["Save this edition", "Hide this edition"],
    ],
  );
});

test("search plays only the tapped song; every other list carries on", () => {
  const list = [{ t: "a" }, { t: "b" }, { t: "c" }];
  // The tapped song is marked, so nothing similar follows it on its own.
  assert.deepEqual(tapQueue("search", list, 1), {
    queue: [{ t: "b", search_play: true }],
    index: 0,
  });
  for (const page of ["library", "playlist", "chart", "mix", "rewind"])
    assert.deepEqual(tapQueue(page, list, 1), { queue: list, index: 1 });
});
