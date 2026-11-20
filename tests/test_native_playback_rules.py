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


def test_a_changed_song_stops_playback_only_when_it_is_queued():
    """#76 (D-56): any changed cached or downloaded song stopped playback and
    emptied the queue, even when it wasn't playing or queued."""
    player = (APP / "PlayerController.swift").read_text()
    invalidate = body(
        player, "func invalidateSources(changed: Set<String>, fresh: [String: Track])"
    )
    queued = invalidate.index("queue.items.contains(where: { changed.contains")
    assert queued < invalidate.index("stop()")
    assert "forget(ids: changed)" in invalidate
    # Codex round 2 on batch E (7be8cfa): a queued song takes its fresh copy;
    # playback stops only when the song playing was removed.
    removed = invalidate.index("if refresh.current != .removed {")
    assert queued < removed < invalidate.index("stop()")
    model = (APP / "AppModel.swift").read_text()
    assert "invalidateSources()" not in model
    assert model.count("invalidateSources(changed: changed, fresh: fresh)") == 2


def test_watch_copies_fit_the_watch_budget():
    """#77 (D-57): the phone prepared at least 2 GB of Watch copies while the
    Watch keeps 1 GB, so the rest waited for the Watch for good."""
    phone = (APP / "WatchPhoneController.swift").read_text()
    assert "2 * 1024 * 1024 * 1024" not in phone
    assert "receipt?.budgetBytes ?? Self.watchDefaultBudget" in phone


def test_watch_audio_does_not_wait_for_the_manifest_round_trip():
    """#219: the phone sent audio only after the Watch confirmed the current
    manifest, a round trip that needed both apps running."""
    phone = (APP / "WatchPhoneController.swift").read_text()
    publish = phone.split("private func publishOnce(", 1)[1].split(
        "private func deferPublish(", 1
    )[0]
    assert "receipt.manifestApplied else { return }" not in publish
    assert "ahead: ahead, receipt: known, committed: committed" in publish
    assert "context: isAhead ? nextContext : context" in publish


def test_the_watch_manifest_is_built_from_one_reading_of_the_archive():
    """#218: a publish read the records, then the files after two hand-overs
    had deleted them, and listed 5 of 97 songs; the Watch deleted the rest.
    The listing rule itself is tested in PigKit (WatchListingTests)."""
    phone = (APP / "WatchPhoneController.swift").read_text()
    publish = body(phone, "private func publishOnce(")
    assert "downloads.records.filter" not in publish
    assert "localFiles(" not in publish
    assert "await downloads.archive.listing(" in publish
    assert "WatchListing.songs(" in publish
    assert "account: namespace, published, lastWritten, appliedManifest" in publish


def test_a_watch_link_failure_clears_once_a_publish_gets_through():
    """#208: WatchConnectivity's "not installed" stayed on the Watch page long
    after the Watch app was reinstalled; only Retry cleared it."""
    phone = (APP / "WatchPhoneController.swift").read_text()
    publish = body(phone, "private func publishOnce(")
    assert "message = error.userMessage" not in publish
    assert "failPublish(error.userMessage)" in publish
    taken = publish.split("contextSent = context", 1)[1].split("let outstanding", 1)[0]
    assert "publishSucceeded()" in taken
    assert "message = nil" in body(phone, "private func publishSucceeded(")


def test_forgetting_changed_sources_removes_them_before_any_suspension():
    """#76 (Codex round 1): releasing one source suspends the repository
    actor; a song leased meanwhile must not be released by the same pass."""
    repository = (
        ROOT / "swift/PigKit/Sources/PigPlayback/PlaybackSourceRepository.swift"
    ).read_text()
    forget = body(repository, "public func forget(ids: Set<String>)")
    assert not re.search(r"for [^{]* in entries[^{]*\{[^}]*await", forget)
    assert "await preparer.release" in forget


def test_whats_playing_never_stays_on_listening():
    """#124, build 2002: "What's playing?" stayed on Listening… for ever. A
    cancelled attempt on a still-shown screen left it there (`case
    .failure(.cancelled): break`), the old screen's onDisappear ended a new
    screen's attempt, and nothing bounded the whole call. Every step is now
    logged, a screen ends only its own attempt, an outer deadline covers
    identify, and every ending leaves Listening… (the mapping and the
    deadline are PigDomain's, tested with swift test)."""
    search = (APP / "SearchViews.swift").read_text()
    view = search[search.index("private struct ShazamListenView") :]
    view = view[: view.index("/// Where a Shazam song is")]
    assert "case .failure(.cancelled): break" not in view
    # Logged at the tap and as the task's first statement.
    section = search[search.index("private struct ShazamSection") :]
    tap = section[: section.index("listening = true")]
    assert 'DiagnosticsLog.shared.record("shazam"' in tap
    task = body(view, ".task(id: attempt)")
    first = task.split("\n")[1].strip()
    assert first.startswith('DiagnosticsLog.shared.record("shazam"')
    # The whole identify call is bounded, and its end maps to a screen.
    assert "ShazamListening.bounded(ShazamListening.outerLimit)" in task
    assert "identify(owner: owner" in task
    assert "ShazamScreen.after(end, screenClosed: Task.isCancelled || closing)" in task
    # Closing ends only this screen's attempt.
    assert "ShazamListener.shared.cancel()" not in view
    assert ".onDisappear { ShazamListener.shared.cancel(owner: owner) }" in view
    listener = (ROOT / "swift/PigKit/Sources/PigSync/ShazamListener.swift").read_text()
    identify = listener[listener.index("public func identify(") :]
    identify = body(identify, "-> Result<ShazamSong, ShazamFailure>")
    assert identify.split("\n")[1].strip() == (
        'DiagnosticsLog.shared.record("shazam", "identify started")'
    )
    # A match is shown first: the lookup is bounded, the save runs apart.
    assert "ShazamListening.detailed(" in identify
    assert "await Self.detailedSong(item" not in identify
    save = identify[: identify.index("SHLibrary.default.addItems")]
    assert "Task { @MainActor in" in save[save.rindex("if item.shazamID != nil") :]
