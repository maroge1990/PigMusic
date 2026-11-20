"""The iPhone checks a new playlist's name with its own copy of the server's
rule before the playlist enters its outbox (Codex round 2 on batch G): a
name the server refuses would leave the playlist's songs waiting behind a
create that can never apply. One fixture is checked here against the
server and by PigKit's PlaylistNameTests against the Swift copy."""

import json
import re
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.native_mutations import NativeMutations
from server.routes.personal import NativeMutationInput

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/playlist_names.json"
SWIFT = ROOT / "swift/PigKit/Sources/PigDomain/OfflineMutation.swift"


def names():
    return json.loads(FIXTURE.read_text())["names"]


def server_keeps(name):
    """What the server stores for `name` sent in a create, or None."""
    try:
        NativeMutationInput(
            operation_id="00000000-0000-0000-0000-000000000000",
            action="create",
            name=name,
        )
        return NativeMutations.name(name)
    except (ValueError, ValidationError):
        return None


@pytest.mark.parametrize("case", names(), ids=lambda case: repr(case["name"])[:30])
def test_the_server_keeps_each_name_as_the_fixture_says(case):
    accepted = case["accepted"]
    if accepted is None:
        # Refused as typed, and nothing the iPhone could send instead.
        assert server_keeps(case["name"]) is None
        assert server_keeps(case["name"].strip()) is None
    else:
        # The iPhone sends the name already stripped; the server keeps it.
        assert server_keeps(case["name"]) in (accepted, None)
        assert server_keeps(accepted) == accepted


def test_the_swift_rule_strips_what_python_strips():
    if not SWIFT.exists():
        pytest.skip("native sources are not in this image")
    source = SWIFT.read_text().split("public static let pythonSpaces", 1)[1]
    listed = {
        int(value, 16)
        for value in re.findall(r"0x([0-9A-F]+)", source.split("]", 1)[0])
    }
    spaces = {c for c in range(sys.maxunicode + 1) if chr(c).isspace()}
    assert listed == spaces
    assert "tests/fixtures/playlist_names.json" in (
        ROOT / "swift/PigKit/Tests/PigCoreTests/PlaylistNameTests.swift"
    ).read_text()
