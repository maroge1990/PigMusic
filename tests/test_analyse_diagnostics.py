import json

from scripts import analyse_diagnostics

EXPORT = """PigMusic diagnostics · 2026-10-26T04:57:31Z
iPhone app 0.2.0 (10) · iOS 27.0.1

== iPhone log ==
2026-10-26T08:00:00.000+11:00 [lifecycle] iPhone 0.2.0 (10) launched
2026-10-26T08:00:01.000+11:00 [lifecycle] previous run ended unexpectedly (crash or termination)
2026-10-26T08:00:02.000+11:00 [playback] address in 17668 ms
2026-10-26T08:00:03.000+11:00 [playback] play: sound after 18000 ms (seamless)
2026-10-26T08:00:04.000+11:00 [playback] streaming instead: slow start
2026-10-26T08:00:05.000+11:00 [network] playback_url 200 in 16211 ms, server 16100 ms: mix-lease 16050 (id 0123456789abcdef)
2026-10-26T08:01:00.000+11:00 [playback] passed over an unavailable song
2026-10-26T08:01:00.500+11:00 [playback] passed over an unavailable song
2026-10-26T08:01:01.000+11:00 [playback] passed over an unavailable song
2026-10-26T08:02:00.000+11:00 [sync] refreshed library in 46000 ms
2026-10-26T08:03:00.000+11:00 [sync] refresh failed: Can't reach your server. Check your connection and try again.
2026-10-26T08:04:00.000+11:00 [watch] Watch confirmed revision 24: 1 songs ready, manifest applied=true
2026-10-26T08:09:00.000+11:00 [watch] Watch confirmed revision 24: 6 songs ready, manifest applied=true
2026-10-26T08:09:01.000+11:00 [watch] Watch log received (0.2.0 (10)); its previous run ended unexpectedly
2026-10-26T08:09:02.000+11:00 [link] reachable=true
2026-10-26T08:09:03.000+11:00 [link] reachable=false
2026-10-26T08:09:04.000+11:00 [link] file transfer finished

== Watch log (0.2.0 (10), sent 2026-10-26T00:56:27Z) ==
2026-10-26T08:05:00.000+11:00 [lifecycle] Watch 0.2.0 (10) launched
2026-10-26T08:05:00.100+11:00 [sync] context revision 24 for another account
2026-10-26T08:05:00.200+11:00 [sync] context revision 24 for another account

== crash-1.json ==
{"crashDiagnostics": [{"diagnosticMetaData": {"signal": 9, "terminationReason": "<RBSTerminateContext| domain:10 code:0x8BADF00D explanation:scene-update watchdog transgression: app is stuck>"}}]}
"""


def test_findings_name_what_a_person_lived_through():
    report = analyse_diagnostics.analyse(EXPORT)
    text = "\n".join(report["findings"])
    assert "A play (seamless) took 18.0 s to make sound" in text
    assert "playback address took 17.7 s" in text
    assert "3 songs were passed over within seconds" in text
    assert "1× slow start" in text
    assert "A library refresh took 46 s" in text
    assert "1 library refreshes failed: Can't reach your server" in text
    assert "The iPhone app ended unexpectedly 1 times" in text
    assert "Watch revision 24: 6 songs ready over 5.0 min (1.0 songs a minute)" in text
    assert "context revision 24 2 times within a second" in text
    assert "'for another account' arrived 2 times" in text
    assert (
        "0x8BADF00D scene-update watchdog transgression" in text and "signal 9" in text
    )
    (slow,) = report["iphone"]["playback"]["slowest_requests"]
    assert slow["id"] == "0123456789abcdef" and slow["ms"] == 16211
    assert report["watch"]["reachability_changes"] == 1
    assert report["watch"]["log_sent"] == "2026-10-26T00:56:27Z"
    json.dumps(report, default=str)


def test_since_keeps_only_later_entries(tmp_path, capsys):
    export = tmp_path / "export.txt"
    export.write_text(EXPORT)
    analyse_diagnostics.main(
        [str(export), "--since", "2026-10-26T08:03:30+11:00", "--json"]
    )
    report = json.loads(capsys.readouterr().out)
    assert report["iphone"]["playback"]["address_ms"] is None
    assert report["iphone"]["lifecycle"].get("unexpected ends") is None
    assert report["watch"]["context_for_another_account"] == 2


def test_an_unreadable_crash_report_is_said_so():
    report = analyse_diagnostics.analyse("== crash-2.json ==\n{not json\n")
    assert report["findings"] == ["Crash report crash-2.json: unreadable."]
