"""Playback and Watch delivery rules in the iPhone app's Swift sources (the
app target has no runtime tests; PigKit's parts are tested with swift test,
device checks confirm behaviour, T5)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "swift/PigMusic"

# The container image tests mount only tests/; the app sources are absent
# there and these checks run wherever the checkout is whole.
pytestmark = pytest.mark.skipif(
    not APP.exists(), reason="native app sources are not in this image"
)


def body(source, signature):
    """The text of one Swift function, by brace matching."""
    start = source.index(signature)
    depth, i = 0, source.index("{", start)
    for j in range(i, len(source)):
        depth += {"{": 1, "}": -1}.get(source[j], 0)
        if depth == 0:
            return source[i : j + 1]
    raise AssertionError(signature)


def test_a_route_change_while_paused_rebuilds_nothing_until_play():
    """#78: a paused engine never runs, so every route change while paused
    (AirPods out, a Bluetooth reconnect) re-fetched and decoded the song."""
    player = (APP / "PlayerController.swift").read_text()
    route = body(player, "private func recoverRoute()")
    owed = route.index("guard intent.wantsPlayback else")
    assert owed < route.index("recoverOutput()")
    assert "outputOwed = true" in route[owed:]
    resume = body(player, "func resume()")
    assert re.search(
        r"if outputOwed \{\s*outputOwed = false\s*engine\?\.recoverOutput\(\)", resume
    )
