import gc

import pytest

from server.catalogue import Catalogue
from server.popularity import Popularity


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_rate_limits: keep the production provider request spacing"
    )
    config.addinivalue_line(
        "markers", "loop_timing: measures event-loop stalls against a short limit"
    )
    config.addinivalue_line(
        "markers", "deezer_art: asks Deezer for artwork (through the test's transport)"
    )


@pytest.fixture(autouse=True)
def collect_before_loop_timing(request):
    # By mid-suite a full collection takes ~0.4 s locally and over 0.5 s on CI
    # runners, enough to fail a 0.2 s stall limit at random (CI run
    # 41014086837). Collect first so the pause can't land in the measurement.
    if request.node.get_closest_marker("loop_timing"):
        gc.collect()


@pytest.fixture(autouse=True)
def rebuild_at_once(monkeypatch):
    # A publication's library rebuild is debounced in the app (#136); tests
    # that read the library straight after one get it at once. The debounce
    # has tests of its own, which set it back.
    monkeypatch.setattr("server.store.REBUILD_DEBOUNCE", 0, raising=False)


@pytest.fixture(autouse=True)
def isolate_startup_enrichment(monkeypatch, tmp_path_factory, request):
    # Provider tests supply their own mock HTTP transport after app startup.
    # Keep unrelated background requests out of those explicit scenarios.
    monkeypatch.setenv("PIG_MUSIC_PRELOAD", "false")

    # The default /library resolves to the host's /Library on macOS, which the
    # startup scanner would walk. Tests that need a library set their own.
    monkeypatch.setenv(
        "PIG_MUSIC_LIBRARY", str(tmp_path_factory.getbasetemp() / "no-library")
    )

    # Mocked providers need no politeness delay; test_rate_limits covers it.
    if not request.node.get_closest_marker("real_rate_limits"):
        monkeypatch.setattr(Catalogue, "min_interval", 0)
        monkeypatch.setattr(Catalogue, "retry_pause", 0)
        monkeypatch.setattr(Popularity, "min_interval", 0)

    # Legacy functional fixtures are deliberately unauthenticated and isolated.
    # Security tests explicitly revoke this bypass; runtime strips it.
    monkeypatch.setenv("PIG_MUSIC_TEST_LEGACY_API", "1")


@pytest.fixture(autouse=True)
def youtube_stays_offline(monkeypatch):
    # The app asks YouTube with JioSaavn (#193). No test may reach YouTube:
    # its search requests and any worker lookup of a video are refused here;
    # a test that wants YouTube supplies a fake (tests/test_youtube.py).
    from server import youtube
    from server.providers.contracts import ProviderError

    def fetch(*args, **kwargs):
        raise ProviderError("not_configured")

    ask = getattr(youtube.YouTube, "ask", None)

    def offline_ask(self, request, update):
        if "video_id" in request:
            return {"error": "not_configured"}
        return ask(self, request, update)

    monkeypatch.setattr(youtube, "fetch", fetch)
    if ask is not None:
        monkeypatch.setattr(youtube.YouTube, "ask", offline_ask)


@pytest.fixture(autouse=True)
def deezer_art_stays_quiet(monkeypatch, request):
    # Deezer is asked first for covers and artist photos (#194). Other tests
    # count their MusicBrainz, archive and Fanart requests, so Deezer finds
    # nothing unless a test is marked deezer_art (tests/test_deezer_art.py).
    if request.node.get_closest_marker("deezer_art"):
        return
    from server.deezer_art import DeezerArt

    async def nothing(self, *args, **kwargs):
        return None

    monkeypatch.setattr(DeezerArt, "cover", nothing)
    monkeypatch.setattr(DeezerArt, "photo", nothing)
