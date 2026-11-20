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
    assert publish.rstrip().endswith("if handed > 0 { wakeWatch() }")
    wake = body(phone, "  private func wakeWatch(now: Date = Date()) {")
    assert "pushes.handed(" in wake and "watchAppOpen: link.reachable" in wake
    # Sent only on the rule's say-so, and counted only once it went.
    assert wake.index("decision == .send") < wake.index("link.pushComplication(data)")
    assert wake.index("link.pushComplication(data)") < wake.index(
        "pushes.sent(at: now)"
    )
    assert "CompanionWake(sentAt: now)" in wake
    # Saved, so a background launch keeps the clock.
    assert phone.count("Self.complicationPushesKey") == 2
    # One place sends it, on the iPhone only, and only with the complication on the face.
    sources = "".join(path.read_text() for path in (ROOT / "swift").rglob("*.swift"))
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
        "if CompanionWake.decode(data) != nil {\n          self?.wokenByPhone()"
    )
    wake = body(model, "  func wokenByPhone(at date: Date = Date()) {")
    # Noted first (it reopens the refresh window, #300), then the delivery
    # runs apart from the message, which it would otherwise wait for.
    assert "$0.phonePushed(active: active, at: date)" in wake
    assert "guard phoneWake == nil else { return }" in wake
    task = wake.split("phoneWake = Task {", 1)[1]
    assert task.index("await self?.finishBackgroundDelivery()") < task.index(
        "self?.planRefresh()"
    )
