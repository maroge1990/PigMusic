import gc
import logging
import os
import shutil
import tempfile

import pytest

from server.catalogue import Catalogue
from server.popularity import Popularity


def pytest_configure(config):
    # server.app builds its app at import, so each xdist worker creates the
    # database in PIG_MUSIC_DATA while collecting. Sharing one folder, four
    # workers raced to create it ("database is locked" at collection, #189).
    # Give every process its own, before collection imports the app.
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    config.pig_music_data = tempfile.mkdtemp(prefix=f"pig-music-data-{worker}-")
    os.environ["PIG_MUSIC_DATA"] = config.pig_music_data
    config.addinivalue_line(
        "markers", "real_rate_limits: keep the production provider request spacing"
    )
    config.addinivalue_line(
        "markers", "loop_timing: measures event-loop stalls against a short limit"
    )
    config.addinivalue_line(
        "markers", "deezer_art: asks Deezer for artwork (through the test's transport)"
    )


def pytest_unconfigure(config):
    shutil.rmtree(getattr(config, "pig_music_data", ""), ignore_errors=True)


class UnhandledErrors(logging.Handler):
    """Collects what server.errors.ServerErrors logs for an error no route
    handled: the line behind every bare 500 (#244)."""

    def __init__(self):
        super().__init__()
        self.seen = []

    def emit(self, record):
        if record.getMessage().startswith("unhandled error"):
            self.seen.append(record.getMessage())


def guarded(item):
    """Runs one phase of a test with the guard listening. A test that met an
    unhandled server error fails, whatever it asserted, unless it is marked
    `server_error_expected`: #235's KeyError was a 500 that no test saw."""
    logger = logging.getLogger("pig_music.errors")
    handler = UnhandledErrors()
    logger.addHandler(handler)
    try:
        result = yield
    finally:
        logger.removeHandler(handler)
    if handler.seen and not item.get_closest_marker("server_error_expected"):
        raise AssertionError(
            "The server answered an unhandled error during this test (mark it "
            "server_error_expected if that is what it tests):\n"
            + "\n".join(handler.seen)
        )
    return result


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    return (yield from guarded(item))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    return (yield from guarded(item))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    return (yield from guarded(item))


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
