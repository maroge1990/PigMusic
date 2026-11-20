"""#219: the Watch notes when watchOS gives it time and sends that with its
log (Swift sources; the rules themselves are tested in PigKit)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "swift/PigMusicWatch/PigMusicWatchApp.swift"
MODEL = ROOT / "swift/PigMusicWatch/WatchModel.swift"
LINK = ROOT / "swift/Shared/CompanionLink.swift"

pytestmark = pytest.mark.skipif(
    not APP.exists(), reason="native app sources are not in this image"
)


def body(source: str, signature: str) -> str:
    return source.split(signature, 1)[1].split("\n  }\n", 1)[0]


def test_the_watch_counts_its_wakes_and_sends_them_with_its_log():
    app = APP.read_text()
    model = MODEL.read_text()
    link = LINK.read_text()
    # The iPhone link's background task is counted, then finishes delivery.
    assert (
        ".backgroundTask(.watchConnectivity) { await model.linkBackgroundTask() }"
        in app
    )
    task = body(model, "  func linkBackgroundTask() async {")
    assert "await finishBackgroundDelivery()" in task
    assert "$0.linkTask(pending:" in task
    # Launches, openings, files received and songs accepted are noted.
    assert "$0.launched(background: background)" in model
    assert "model.opened()" in app
    assert "self.onReceived?(envelope)" in link
    assert "$0.received()" in model
    assert "$0.accepted(receivedAt: receivedAt, active: active)" in model
    # Saved, so a background launch adds to the same record.
    assert model.count('"watch.wake-log"') == 2
    # The summary goes above the log the iPhone puts in its export.
    send = body(model, "  func sendDiagnostics() {")
    assert "wakeLog.summary()" in send
    assert "maximumTextBytes - wakes.utf8.count" in send
