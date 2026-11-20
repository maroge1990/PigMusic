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


def test_the_swift_test_reads_the_same_fixture():
    swift = (
        Path(__file__).resolve().parents[1]
        / "swift/PigKit/Tests/PigCoreTests/FavouriteMatchTests.swift"
    )
    if not swift.exists():
        pytest.skip("native sources are not in this image")
    assert "tests/fixtures/favourite_same_song.json" in swift.read_text()
