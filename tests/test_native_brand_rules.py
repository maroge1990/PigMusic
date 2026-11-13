"""#180: the iPhone app against brand 1.8 sections 8, 14 and 16 (Swift
sources; the simulator tap-through tests and screens on the Mac show the
result)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "swift/PigMusic"

pytestmark = pytest.mark.skipif(
    not APP.exists(), reason="native app sources are not in this image"
)


def source(name):
    return (APP / name).read_text()


def test_the_splash_ends_when_the_saved_library_is_shown():
    """Brand 1.8 section 8: the splash shows only while loading, with no
    minimum time. It waited up to 1.5 s after a restored account attached."""
    app = source("PigMusicApp.swift")
    scene = app[app.index("var body: some Scene") : app.index("struct RootView")]
    assert "Task.sleep" not in scene
    assert re.search(r"onChange\(of: model\.launched\)[^}]*finishStarting\(\)", app)
    model = source("AppModel.swift")
    restore = model[model.index("func restore() async") :]
    restore = restore[: restore.index("\n  func ")]
    # Launch is over once the saved library is attached, before the refresh,
    # and on every other way out of restore (sign-in form, demo, failure).
    attached = restore.index("try await attach(connection.person")
    assert attached < restore.index("launched = true", attached)
    assert restore.index("launched = true", attached) < restore.index("await refresh()")
    assert re.search(r"defer \{[^}]*launched = true", restore)
