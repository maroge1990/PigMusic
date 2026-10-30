import type { BrowserContext, Page, Route } from "@playwright/test";

/** A small signed-in household: one artist with a Fanart photo and one album. */
export const artist = {
  id: "artist-1",
  name: "Charli xcx",
  album_ids: ["album-1"],
  album_count: 1,
  track_count: 2,
  compilation_only: false,
};
const track = (id: string, title: string, number: number) => ({
  id,
  title,
  artist: artist.name,
  album: "Brat",
  album_artist: artist.name,
  album_id: "album-1",
  duration: 180,
  track_number: number,
  disc_number: 1,
  server_state: "in_library",
});
export const album = {
  id: "album-1",
  title: "Brat",
  artist: artist.name,
  artist_id: artist.id,
  category: "album",
  track_count: 2,
  cover_url: "/api/v1/artwork/cover-1",
  tracks: [track("t1", "Von dutch", 1), track("t2", "360", 2)],
};
const photo = `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e8e8e8"/><stop offset="1" stop-color="#888"/></linearGradient></defs><rect width="1024" height="1024" fill="url(#g)"/></svg>`;

export type Mock = { appearance?: string; unknown: string[] };

/** Routes every /api call. Unknown calls answer 404 and are listed for the test. */
export async function mockApi(
  /** A context also answers the service worker's own requests. */
  page: Page | BrowserContext,
  options: {
    appearance?: string;
    role?: "owner" | "member";
    /** Answers for specific paths (after /api), checked before the defaults. */
    answers?: Record<string, (route: Route) => Promise<void>>;
  } = {},
) {
  const mock: Mock = { appearance: options.appearance, unknown: [] };
  const json = (route: Route, body: unknown) => route.fulfill({ json: body });
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api/, "");
    const answer = options.answers?.[path];
    if (answer) return answer(route);
    if (path === "/v1/setup/owner")
      return json(route, { setup_required: false });
    if (path === "/v1/me")
      return json(route, {
        id: "u1",
        username: "pat",
        display_name: "Pat",
        role: options.role ?? "member",
        session_id: "session-1",
      });
    if (path === "/v1/preferences")
      return json(route, {
        appearance: mock.appearance ?? "system",
        default_tab: "artists",
      });
    if (path === "/v1/events")
      return route.fulfill({
        contentType: "text/event-stream",
        body: ": ok\n\n",
      });
    if (path === "/v1/library" || path.startsWith("/v1/library?"))
      return json(route, {
        media: {
          portraits: true,
          fanart_configured: true,
          remote_playback: false,
          listenbrainz_configured: false,
        },
        artists: [artist],
        albums: [album],
        playlist_count: 0,
        stats: {
          tracks: 2,
          artists: 1,
          albums: 1,
          compilations: 0,
          soundtracks: 0,
          musicals: 0,
        },
        snapshot: {
          source: "mock",
          captured_at: "2026-10-15T00:00:00Z",
          read_failures: [],
        },
      });
    if (path === `/v1/artists/${artist.id}`)
      return json(route, {
        ...artist,
        albums: [album],
        compilation_tracks: [],
      });
    if (path === "/v1/albums/album-1") return json(route, album);
    if (path === "/v1/artwork/cover-1")
      return route.fulfill({ contentType: "image/svg+xml", body: photo });
    if (
      /^\/v1\/(catalogue\/)?artists\/[^/]+\/(background|logo)$/.test(path) &&
      path.endsWith("background")
    )
      return route.fulfill({ contentType: "image/svg+xml", body: photo });
    if (path === `/v1/catalogue/artists/${artist.id}/popularity`)
      return json(route, {
        enabled: false,
        songs: [],
        albums: [],
        ranked_album_ids: [],
      });
    if (path === `/v1/catalogue/artists/${artist.id}/releases`)
      return json(route, {
        total: 30,
        offset: 0,
        releases: Array.from({ length: 30 }, (_, i) => ({
          id: `group-${i}`,
          title: `Album ${i + 1}`,
          date: String(2000 + i),
          type: "album",
        })),
      });
    if (path === `/v1/artists/${artist.id}/acquisition`)
      return json(route, { status: "none", monitor: false, kinds: ["album"] });
    if (path === "/v1/requests" || path.startsWith("/v1/requests?"))
      return json(route, {
        items: [],
        next_offset: null,
        active: 0,
        downloads_enabled: true,
      });
    if (path === "/v1/playlists") return json(route, { playlists: [] });
    if (path === "/v1/home/mixes")
      return json(route, { mixes: [], preparing: false });
    if (path === "/v1/admin/mixes")
      return json(route, {
        budget_bytes: 50000000000,
        reserve_bytes: 2000000000,
        profile: "mp3_320",
        timezone: "Australia/Melbourne",
        files: 0,
        used_bytes: 0,
        ready: 0,
        preparing: 0,
        retrying: 0,
      });
    if (path.startsWith("/v1/home/"))
      return json(route, {
        state: "ready",
        title: "",
        artists: [],
        albums: [],
        songs: [],
      });
    mock.unknown.push(route.request().method() + " " + path);
    return route.fulfill({
      status: 404,
      json: { detail: "Not mocked", code: "not_found" },
    });
  });
  return mock;
}
