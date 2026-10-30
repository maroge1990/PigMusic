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


@pytest.fixture(autouse=True)
def collect_before_loop_timing(request):
    # By mid-suite a full collection takes ~0.4 s locally and over 0.5 s on CI
    # runners, enough to fail a 0.2 s stall limit at random (CI run
    # 41014086837). Collect first so the pause can't land in the measurement.
    if request.node.get_closest_marker("loop_timing"):
        gc.collect()


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
