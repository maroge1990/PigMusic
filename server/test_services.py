"""T3 test rig only (#15): send the server's metadata and artwork requests to
fake services instead of MusicBrainz, ListenBrainz, Cover Art Archive, the
Apple chart and the artist-image hosts.

Off unless PIG_MUSIC_TEST_SERVICES names the fake's base URL. While it is on,
a request for any other host is refused, so a rig never reaches a real
provider. Download sources use their own hardened clients and are not
redirected; a rig runs its engine without connecting.
"""

import os

import httpx

HOSTS = frozenset(
    {
        "musicbrainz.org",
        "api.listenbrainz.org",
        "labs.api.listenbrainz.org",
        "coverartarchive.org",
        "rss.marketingtools.apple.com",
        "webservice.fanart.tv",
        "assets.fanart.tv",
        "artists.artistgrid.cx",
        "assets.artistgrid.cx",
    }
)


class FakeServices(httpx.AsyncBaseTransport):
    """Rewrites https://<host>/<path> to <base>/<host>/<path>."""

    def __init__(self, base, inner=None):
        self.base = httpx.URL(base)
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        host = request.url.host
        if host not in HOSTS:
            raise httpx.ConnectError(
                "T3 rig: no real provider requests (" + host + ")", request=request
            )
        request.url = self.base.copy_with(
            path=self.base.path.rstrip("/") + "/" + host + request.url.path,
            query=request.url.query,
        )
        request.headers["host"] = self.base.netloc.decode()
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


def transport():
    """The fake-services transport when the rig asks for one, else None."""
    base = os.environ.get("PIG_MUSIC_TEST_SERVICES", "").strip()
    if not base:
        return None
    print("PigMusic T3 rig: metadata requests go to " + base, flush=True)
    return FakeServices(base)
