import json

import pytest

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


def test_bad_lines_and_crash_reports_are_reported_not_fatal():
    deep = "[" * 1200 + "]" * 1200
    export = (
        "== iPhone log ==\n"
        "2026-13-26T08:00:00Z [playback] address in 900 ms\n"
        "2026-10-26T08:00:01.000+11:00 [playback] address in 120 ms\n"
        "2026-10-26T08:00:02.000+11:00 [network] playback_url URLError -1001 in 120000 ms (id 0123456789abcdef)\n"
        "2026-10-26T08:00:03.000+11:00 [network] me_api 502 in 40 ms (id fedcba9876543210)\n"
        f"== crash-deep.json ==\n{deep}\n"
    )
    report = analyse_diagnostics.analyse(export)
    assert report["malformed_lines"] == 1
    assert report["iphone"]["playback"]["address_ms"]["count"] == 1
    slowest = report["iphone"]["playback"]["slowest_requests"][0]
    # A timeout is the case that matters most; its ID must survive.
    assert (
        slowest["outcome"] == "URLError -1001" and slowest["id"] == "0123456789abcdef"
    )
    text = "\n".join(report["findings"])
    assert "Requests failed: 1× URLError -1001, 1× 502" in text
    assert "Crash report crash-deep.json: unreadable." in text
    assert "1 lines had an impossible time and were left out." in text


def test_a_file_far_larger_than_an_export_is_not_read(tmp_path, monkeypatch):
    export = tmp_path / "huge.txt"
    export.write_text("x" * 100)
    monkeypatch.setattr(analyse_diagnostics, "MAX_EXPORT_BYTES", 50)
    try:
        analyse_diagnostics.main([str(export)])
    except SystemExit as exc:
        assert "larger than any diagnostics export" in str(exc)
    else:
        raise AssertionError("an oversized file was read")


@pytest.mark.parametrize("status", [401, 403, 429])
def test_client_errors_are_failed_requests_even_when_they_are_fast(status):
    report = analyse_diagnostics.analyse(
        "== iPhone log ==\n"
        f"2026-10-28T10:00:00+11:00 [network] playback_url {status} in 12 ms (id 0123456789abcdef)\n"
    )
    assert any(f"Requests failed: 1× {status}" in item for item in report["findings"])
    assert report["iphone"]["playback"]["requests"][0]["id"] == "0123456789abcdef"


def test_success_and_redirect_statuses_are_not_failed_requests():
    lines = [
        f"2026-10-28T10:00:0{index}+11:00 [network] media {status} in 12 ms (id 0123456789abcdef)"
        for index, status in enumerate([200, 204, 206, 301, 304])
    ]
    report = analyse_diagnostics.analyse("== iPhone log ==\n" + "\n".join(lines))
    assert not any(item.startswith("Requests failed:") for item in report["findings"])
