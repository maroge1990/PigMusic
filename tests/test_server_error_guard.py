"""The suite-wide guard against unhandled server errors (#244, conftest.py).

#235's KeyError answered a 500 that no test looked for. The guard fails any
test during which the server logged an unhandled error, unless the test is
marked `server_error_expected`. These tests run one phase of the guard by
hand around a route that raises; they provoke that error themselves, so they
carry the marker for the guard around them.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import guarded
from server import errors


class Item:
    def __init__(self, marked):
        self.marked = marked

    def get_closest_marker(self, name):
        return self.marked if name == "server_error_expected" else None


def raising_app():
    app = FastAPI()
    errors.install(app)

    @app.get("/broken")
    def broken():
        raise KeyError("musicbrainz_release_id")

    @app.get("/fine")
    def fine():
        return {"ok": True}

    return TestClient(app)


def run_guarded(item, path):
    phase = guarded(item)
    next(phase)
    answer = raising_app().get(path)
    try:
        phase.send(None)
    except StopIteration:
        pass
    return answer


@pytest.mark.server_error_expected
def test_a_test_that_met_an_unhandled_server_error_fails():
    # A test that never looks at the status still fails.
    with pytest.raises(AssertionError, match="GET /broken answered 500"):
        run_guarded(Item(marked=False), "/broken")


@pytest.mark.server_error_expected
def test_a_marked_test_may_meet_one():
    answer = run_guarded(Item(marked=True), "/broken")
    assert answer.status_code == 500
    assert answer.json()["code"] == "server_error"


def test_a_test_without_one_passes():
    assert run_guarded(Item(marked=False), "/fine").json() == {"ok": True}
