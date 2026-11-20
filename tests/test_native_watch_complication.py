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
