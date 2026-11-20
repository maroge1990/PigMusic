"""D-125, #219: the Watch app writes its status for the watch-face
complication, which reads it from the App Group (Swift sources; the rules
themselves are tested in PigKit, WatchComplicationTests)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "swift/PigMusicWatch/WatchModel.swift"
WIDGET = ROOT / "swift/PigMusicWatchComplication/StatusComplication.swift"

pytestmark = pytest.mark.skipif(
    not MODEL.exists(), reason="native app sources are not in this image"
)


def body(source: str, signature: str) -> str:
    return source.split(signature, 1)[1].split("\n  }\n", 1)[0]


def test_the_watch_writes_its_status_where_the_complication_reads_it():
    model = MODEL.read_text()
    widget = WIDGET.read_text()
    group = (
        "containerURL(\n    forSecurityApplicationGroupIdentifier: "
        "WatchComplicationState.appGroup\n  )?.appendingPathComponent("
        "WatchComplicationState.fileName)"
    )
    assert group in model
    assert (
        "forSecurityApplicationGroupIdentifier: WatchComplicationState.appGroup)"
        in (widget)
    )
    assert "appendingPathComponent(WatchComplicationState.fileName)" in widget
    update = body(model, "  func updateComplication(now: Date = Date()) {")
    # The home's own status, written whole (atomically) only when what the
    # complication shows changed.
    assert "WatchComplicationState(status: status, at: now)" in update
    assert "!state.shows(sameAs: complicationWritten)" in update
    assert "write(to: file, options: .atomic)" in update
    # Redrawn only as the throttle says, and only this widget.
    assert "complicationReload.changed(to: state, now: now)" in update
    now = update.split("case .now:", 1)[1].split("case .later", 1)[0]
    assert (
        "WidgetCenter.shared.reloadTimelines(ofKind: WatchComplicationState.widgetKind)"
        in now
    )
    assert model.count("reloadTimelines(") == 1
    assert "reloadAllTimelines" not in model
    later = update.split("case .later(let date):", 1)[1]
    assert "guard complicationRedraw == nil else { return }" in later
    # Every change of the receipt or the library recomputes it.
    reload = body(model, "  func reload() async {")
    assert "receipt = next\n    updateComplication()" in reload
    acknowledge = body(model, "  private func acknowledge() async {")
    assert "self.receipt = receipt\n    updateComplication()" in acknowledge
    # The widget is the kind the app reloads, in the families D-125 asks for.
    assert "StaticConfiguration(kind: WatchComplicationState.widgetKind" in widget
    for family in (".accessoryCircular", ".accessoryCorner"):
        assert family in widget
    # Its words and icons come from PigKit, not the widget.
    assert "entry.state?.words" in widget and "entry.state?.symbol" in widget
    code = [line for line in widget.splitlines() if not line.strip().startswith("//")]
    for words in (
        "Ready to play",
        "waiting",
        "Storage full",
        "Can't reach",
        "Couldn't",
    ):
        assert not any(words in line for line in code)


PHONE = ROOT / "swift/PigMusic/WatchPhoneController.swift"
LINK = ROOT / "swift/Shared/CompanionLink.swift"


def test_the_iphone_wakes_the_watch_app_through_the_budget():
    """After a publish hands songs to the link, one complication push may
    wake the Watch app, only as WatchComplicationPushes allows."""
    phone = PHONE.read_text()
    link = LINK.read_text()
    publish = phone.split("  private func publishOnce(force: Bool) async {", 1)[1]
    publish = publish.split("\n  }\n", 1)[0]
    assert publish.rstrip().endswith("if handed > 0 { wakeWatch(songs: handedIds) }")
    assert "handed += 1\n        handedIds.append(song.id)" in publish
    wake = body(
        phone, "  private func wakeWatch(songs: [String], now: Date = Date()) {"
    )
    assert "pushes.handed(\n      now: now, songs: songs," in wake
    assert "watchAppOpen: link.reachable" in wake
    # Sent only on the rule's say-so, and counted only once it went.
    assert wake.index("decision == .send") < wake.index("link.pushComplication(data)")
    # Exactly the ids in the payload wait as pending (Codex round 2 on J).
    assert wake.index("link.pushComplication(data)") < wake.index(
        "pushes.sent(at: now, songs: wake.songs ?? [])"
    )
    # The push names only songs no earlier push announced or holds (round 1).
    assert (
        "songs.filter { pushes.pushedSongs[$0] == nil && pushes.pendingSongs[$0] == nil }"
        in wake
    )
    assert "let wake = CompanionWake(sentAt: now, songs: fresh)" in wake
    assert "JSONEncoder().encode(wake)" in wake
    # WatchConnectivity's report settles them: delivered or released.
    finished = body(phone, "  func wakeFinished(_ data: Data, delivered: Bool) {")
    assert (
        "pushes.finished(songs: wake.songs ?? [], sentAt: wake.sentAt, delivered: delivered)"
        in finished
    )
    assert "self?.wakeFinished(data, delivered: delivered)" in phone
    report = link.split("didFinish userInfoTransfer: WCSessionUserInfoTransfer", 1)[1]
    report = report.split("\n    }\n", 1)[0]
    assert "guard userInfoTransfer.isCurrentComplicationInfo" in report
    assert "let delivered = error == nil" in report
    assert "self.onWakeFinished?(data, delivered)" in report
    ios = link.split("  #if os(iOS)\n    nonisolated func sessionDidBecomeInactive", 1)[
        1
    ]
    assert "didFinish userInfoTransfer" in ios.split("  #endif", 1)[0]
    # Saved, so a background launch keeps the clock.
    assert phone.count("Self.complicationPushesKey") == 2
    # One place sends it, on the iPhone only, and only with the complication on the face.
    # Only the source folders: a runner's build folder holds checkouts named
    # like "GRDB.swift" that are directories.
    folders = [
        "PigMusic",
        "PigMusicWatch",
        "PigMusicWatchComplication",
        "PigMusicShare",
        "Shared",
        "PigKit/Sources",
    ]
    sources = "".join(
        path.read_text()
        for folder in folders
        for path in (ROOT / "swift" / folder).rglob("*.swift")
        if path.is_file()
    )
    assert sources.count("transferCurrentComplicationUserInfo(") == 1
    push = link.split("    func pushComplication(_ data: Data) -> Bool {", 1)[1]
    push = push.split("\n    }\n", 1)[0]
    assert "session.isComplicationEnabled" in push
    assert '["payload": data]' in push
    ios = link.split("  #if os(iOS)\n    /// Whether PigMusic's complication", 1)[1]
    assert "func pushComplication" in ios.split("  #endif", 1)[0]


def test_the_watch_takes_songs_when_the_iphone_wakes_it():
    model = MODEL.read_text()
    message = model.split("link?.onMessage = { [weak self] data in", 1)[1]
    assert message.lstrip().startswith(
        "if let wake = CompanionWake.decode(data) {\n          self?.wokenByPhone(wake)"
    )
    wake = body(
        model, "  func wokenByPhone(_ wake: CompanionWake, at date: Date = Date()) {"
    )
    # Noted first, with its songs and send time (only new songs, sent
    # recently, move the window on), then the delivery runs apart from the
    # message, which it would otherwise wait for.
    assert (
        "$0.phonePushed(songs: wake.songs ?? [], sentAt: wake.sentAt, active: active, at: date)"
        in wake
    )
    assert "guard phoneWake == nil else { return }" in wake
    task = wake.split("phoneWake = Task {", 1)[1]
    assert task.index("await self?.finishBackgroundDelivery()") < task.index(
        "self?.planRefresh()"
    )


def test_both_exports_say_whether_the_watch_is_being_woken():
    """The iPhone's Watch delivery section carries the pushes and their
    budget; the Watch's wake summary counts the pushes it got and whether a
    complication is on a face, as WidgetKit says."""
    tools = (ROOT / "swift/PigMusic/AccountToolsViews.swift").read_text()
    section = tools.split('"== Watch delivery =="', 1)[1].split("parts +=", 1)[0]
    assert "watch.complicationPushes.summary(" in section
    assert "enabled: watch.link?.complicationEnabled" in section
    assert "remaining: watch.link?.complicationPushesLeft" in section
    model = MODEL.read_text()
    check = body(model, "  func checkComplications() {")
    assert "WidgetCenter.shared.getCurrentConfigurations" in check
    assert "$0.kind == WatchComplicationState.widgetKind" in check
    assert "$0.complicationsOnFaces(count)" in check
    assert "checkComplications()" in body(model, "  func opened() {")
    send = body(model, "  func sendDiagnostics() {")
    assert send.index("checkComplications()") < send.index("wakeLog.summary()")


def test_reachability_both_ways_redraws_and_a_stale_status_expires():
    """Codex round 1 on batch J: losing the iPhone left the complication's
    old status; and "Can't reach your iPhone" outlived the iPhone's return."""
    link = LINK.read_text()
    change = link.split("  nonisolated func sessionReachabilityDidChange(", 1)[1]
    change = change.split("\n  }\n", 1)[0]
    # Called on both transitions, before the reachable-only hook.
    assert "self.onReachability?()\n      if reachable {" in change
    model = MODEL.read_text()
    hook = "link?.onReachability = { [weak self] in self?.updateComplication() }"
    assert hook in model
    widget = WIDGET.read_text()
    timeline = widget.split("  func getTimeline(", 1)[1].split("\n  }\n", 1)[0]
    assert "StatusEntry(date: now, state: state?.shown(at: now))" in timeline
    assert "entries.append(StatusEntry(date: expires, state: nil))" in timeline
    snapshot = widget.split("  func getSnapshot(", 1)[1].split("\n  }\n", 1)[0]
    assert "Self.load()?.shown(at: now)" in snapshot
