"""The iPhone decides a song's favourite state from the library's Favourites
playlist with its own copy of the server's test (Codex round 1 on batch G).
One fixture of pairs is checked here against the server and by PigKit's
FavouriteMatchTests against the Swift copy, so the two can't drift apart."""

import json
from pathlib import Path

import pytest

from server.matching import same_song

FIXTURE = Path(__file__).resolve().parent / "fixtures/favourite_same_song.json"


def pairs():
    return json.loads(FIXTURE.read_text())["pairs"]


@pytest.mark.parametrize("pair", pairs(), ids=lambda pair: pair["note"])
def test_the_server_answers_each_pair_as_the_fixture_says(pair):
    assert same_song(pair["left"], pair["right"]) is pair["same"]
    assert same_song(pair["right"], pair["left"]) is pair["same"]


def test_the_generated_folding_and_fixture_are_this_pythons():
    """Codex round 2 on batch G: Swift's own case folding differed from
    Python's (ı, ẞ). The Swift tables and the normalise fixture come from
    this interpreter's Unicode data; after an upgrade this fails until
    scripts/generate_favourite_folding.py is run again."""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    script = root / "scripts/generate_favourite_folding.py"
    if not script.exists() or not (root / "swift").exists():
        pytest.skip("the generator and native sources are not in this image")
    spec = importlib.util.spec_from_file_location("folding", script)
    folding = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(folding)
    from server.library import normalise

    # The generator's copy of normalise is the server's.
    for case in json.loads(folding.FIXTURE.read_text())["cases"]:
        assert normalise(case["text"]) == case["normalised"], case["text"]
    assert folding.SWIFT.read_text() == folding.swift_source()
    assert folding.FIXTURE.read_text() == folding.fixture_source()


def test_the_swift_test_reads_the_same_fixture():
    swift = (
        Path(__file__).resolve().parents[1]
        / "swift/PigKit/Tests/PigCoreTests/FavouriteMatchTests.swift"
    )
    if not swift.exists():
        pytest.skip("native sources are not in this image")
    assert "tests/fixtures/favourite_same_song.json" in swift.read_text()
