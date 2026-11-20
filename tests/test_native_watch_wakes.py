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
    assert "let acceptance = try await archive.accept(file, ticket: ticket)" in model
    assert (
        "receivedAt: receivedAt, active: active, newSong: acceptance.newlyReady,\n"
        "              waiting: acceptance.waitingBefore)" in model
    )
    # Saved, so a background launch adds to the same record.
    assert model.count('"watch.wake-log"') == 2
    # The summary goes above the log the iPhone puts in its export.
    send = body(model, "  func sendDiagnostics() {")
    assert "wakeLog.summary()" in send
    assert "maximumTextBytes - wakes.utf8.count" in send


def test_the_watch_schedules_refreshes_only_through_the_plan():
    app = APP.read_text()
    model = MODEL.read_text()
    sources = "".join(
        path.read_text()
        for path in (ROOT / "swift").rglob("*.swift")
        if "Watch" in str(path)
    )
    # The refresh handler sits beside the link's, and takes what waits.
    assert (
        ".backgroundTask(.appRefresh(WatchModel.refreshIdentifier)) "
        "{ await model.backgroundRefresh() }" in app
    )
    refresh = body(model, "  func backgroundRefresh() async {")
    assert "$0.refreshRan()" in refresh
    assert "link?.reconnect()" in refresh
    assert "await finishBackgroundDelivery()" in refresh
    assert refresh.rstrip().endswith("planRefresh()")
    # One place asks watchOS, and only for the time the plan gives.
    assert sources.count("scheduleBackgroundRefresh(") == 1
    plan = body(model, "  func planRefresh(now: Date = Date()) {")
    # The saved log judges the window, so its stop holds until the app is
    # opened (Codex round 1 on batch G).
    assert "WatchRefreshPlan.next(" not in plan
    assert "plan = $0.refreshPlan(now: now, waiting: waiting)" in plan
    assert "let waiting = status.waiting" in plan
    assert "WatchRefreshPlan.request(plan, pending: wakeLog.pendingRefresh" in plan
    assert plan.index("WatchRefreshPlan.request(") < plan.index(
        "scheduleBackgroundRefresh("
    )
    assert "withPreferredDate: date" in plan
    # Asked again after a library or song arrives, after each wake, and when
    # the app goes to the background.
    accepted = model.split("      await acknowledge()\n      // A new library", 1)[1]
    assert accepted.split("\n", 2)[1].strip() == "planRefresh()"
    assert (
        body(model, "  func linkBackgroundTask() async {")
        .rstrip()
        .endswith("planRefresh()")
    )
    background = app.split("if phase == .background {", 1)[1].split("}", 1)[0]
    assert "model.closed()\n            model.planRefresh()" in background
    # Only a copy the Watch didn't have moves the window on, as the archive
    # judges it in the write itself, never this model's receipt.
    audio = model.split('} else if envelope.kind == "audio"', 1)[1].split(
        "DiagnosticsLog.shared.record(", 1
    )[0]
    assert "receipt?" not in audio and "status." not in audio
    assert "acceptance.newlyReady" in audio


def test_a_background_wake_gives_kept_files_a_pass_after_their_manifest():
    """A held song being looked at when its manifest landed was skipped by
    the manifest's own pass, and the task ended without another."""
    finish = body(LINK.read_text(), "  func finishBackgroundDelivery() async {")
    assert "let finished = deliveries.finished" in finish
    assert finish.index("let finished = deliveries.finished") < finish.index(
        "await recoverInbox()"
    )
    assert "deliveries.isEmpty, deliveries.finished == finished" in finish
    # The 20 s cap stays.
    assert ".seconds(20)" in finish


def test_new_songs_sent_reopen_the_refresh_window():
    """D-124 as refined on 20 Nov: a manifest listing songs the archive's
    saved library didn't have reopens the window, checked after it is
    applied and against the archive's snapshot, not memory."""
    model = MODEL.read_text()
    manifest = model.split('if envelope.kind == "manifest" {', 1)[1].split(
        '} else if envelope.kind == "audio"', 1
    )[0]
    assert "let previous = await archive.snapshot()" in manifest
    apply = manifest.index("try await archive.apply(manifest)")
    listed = manifest.index(
        "WatchRefreshPlan.addsSongs(manifest.songs.map(\\.id), over: previous.songs.map(\\.id))"
    )
    assert apply < listed
    assert "$0.newSongsListed()" in manifest[listed:]
