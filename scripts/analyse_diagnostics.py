"""Read an exported PigMusic diagnostics file (iPhone and Watch logs, crash
reports) and summarise what a person lived through: how long songs took to
start and why, what failed, how the Watch kept up, and what looks wrong.

The export holds no tokens or links; this reads it locally and sends nothing.

    .venv/bin/python -m scripts.analyse_diagnostics PigMusic-diagnostics.txt
    .venv/bin/python -m scripts.analyse_diagnostics export.txt --since 2026-10-26T08:00 --json
"""

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

LINE = re.compile(
    r"^(\d{4}-\d\d-\d\dT[\d:.]+[+-]\d\d:\d\d|\d{4}-\d\d-\d\dT[\d:.]+Z) \[(\w+)\] (.*)$"
)
SECTION = re.compile(r"^== (.+?) ==$")
# Exports are a few hundred KB (each log keeps 512 KB at most); anything far
# larger is not an export, and a crash report this big is not read.
MAX_EXPORT_BYTES = 32 * 1024 * 1024
MAX_CRASH_BYTES = 2 * 1024 * 1024


def parse(text):
    """{"header": [...], "devices": {"iPhone": [...], "Watch": [...]},
    "crashes": [{"name", "json"}]} with entries (time, category, message)."""
    result = {
        "header": [],
        "devices": defaultdict(list),
        "crashes": [],
        "watch_sent": None,
        "malformed_lines": 0,
    }
    section, crash = None, None
    for raw in text.splitlines():
        heading = SECTION.match(raw.strip())
        if heading:
            title = heading.group(1)
            crash = None
            if title.startswith("crash"):
                crash = {"name": title, "text": []}
                result["crashes"].append(crash)
                section = "crash"
            elif title.startswith("Watch"):
                section = "Watch"
                sent = re.search(r"sent (\S+)\)", title)
                result["watch_sent"] = sent.group(1) if sent else None
            else:
                section = "iPhone"
            continue
        if section is None:
            if raw.strip():
                result["header"].append(raw.strip())
            continue
        if section == "crash":
            crash["text"].append(raw)
            continue
        found = LINE.match(raw)
        if found:
            try:
                stamp = datetime.fromisoformat(found.group(1).replace("Z", "+00:00"))
            except ValueError:
                # A line with an impossible date is counted, not trusted.
                result["malformed_lines"] += 1
                continue
            result["devices"][section].append((stamp, found.group(2), found.group(3)))
    for crash in result["crashes"]:
        crash["summary"] = crash_summary("\n".join(crash.pop("text")))
    return result


def crash_summary(text):
    """When it happened and how it ended, from a MetricKit crash report.
    Empty when it cannot be read: too large, not JSON, or nested too deep."""
    if len(text) > MAX_CRASH_BYTES:
        return {}
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return {}
    wanted = (
        "signal",
        "exceptionType",
        "exceptionCode",
        "terminationReason",
        "timeStampEnd",
    )
    found = {}
    # Walked with a stack, not recursion: depth cannot end the analysis.
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, inner in value.items():
                if key in wanted and key not in found:
                    found[key] = inner
                pending.append(inner)
        elif isinstance(value, list):
            pending.extend(value)
    reason = found.get("terminationReason")
    if isinstance(reason, str):
        # Keep the code and the explanation, not the whole system report.
        code = re.search(r"code:(0x[0-9A-Fa-f]+)", reason)
        explanation = re.search(r"explanation:([^:>]+)", reason)
        found["terminationReason"] = (
            " ".join(
                part
                for part in (
                    code and code.group(1),
                    explanation and explanation.group(1).strip(),
                )
                if part
            )
            or " ".join(reason.split())[:160]
        )
    return found


def numbers(entries, pattern):
    values = []
    for _, _, message in entries:
        match = re.search(pattern, message)
        if match:
            values.append(float(match.group(1)))
    return values


def spread(values):
    if not values:
        return None
    ordered = sorted(values)
    return {
        "count": len(values),
        "median": round(statistics.median(values)),
        "p90": round(ordered[min(len(ordered) - 1, round(0.9 * (len(ordered) - 1)))]),
        "max": round(max(values)),
    }


def bursts(entries, needle, within=timedelta(seconds=3), least=3):
    """Runs of one message close together: a queue passed over in seconds."""
    runs, current = [], []
    for stamp, _, message in entries:
        if needle not in message:
            continue
        if current and stamp - current[-1] > within:
            if len(current) >= least:
                runs.append((current[0], len(current)))
            current = []
        current.append(stamp)
    if len(current) >= least:
        runs.append((current[0], len(current)))
    return runs


def playback(entries):
    play = defaultdict(list)
    for _, category, message in entries:
        match = re.match(r"(\w+): sound after (\d+) ms \((\w+)\)", message)
        if category == "playback" and match:
            play[f"{match.group(1)} ({match.group(3)})"].append(float(match.group(2)))
    slow_network = []
    for stamp, category, message in entries:
        # The outcome is a status ("200") or a failure ("URLError -1001").
        match = re.match(r"(\S+) (.+?) in (\d+) ms(.*)\(id (\w+)\)$", message)
        if category == "network" and match:
            slow_network.append(
                {
                    "at": stamp.isoformat(timespec="seconds"),
                    "operation": match.group(1),
                    "outcome": match.group(2),
                    "ms": int(match.group(3)),
                    "server": match.group(4).strip(" ,") or None,
                    "id": match.group(5),
                }
            )
    return {
        "time_to_sound_ms": {
            kind: spread(values) for kind, values in sorted(play.items())
        },
        "address_ms": spread(numbers(entries, r"^address in (\d+) ms")),
        "source_answered_ms": spread(numbers(entries, r"^source answered in (\d+) ms")),
        "streaming_instead": dict(
            Counter(
                m.split("streaming instead: ", 1)[1].split(":")[0]
                for _, c, m in entries
                if c == "playback" and m.startswith("streaming instead: ")
            )
        ),
        "engine_failures": dict(
            Counter(
                m.split("error=", 1)[-1]
                for _, c, m in entries
                if c == "playback" and m.startswith("engine failed")
            )
        ),
        "passed_over": sum(
            1 for _, _, m in entries if m == "passed over an unavailable song"
        ),
        "passed_over_bursts": [
            {"at": at.isoformat(timespec="seconds"), "songs": n}
            for at, n in bursts(entries, "passed over an unavailable song")
        ],
        "slowest_requests": sorted(slow_network, key=lambda r: -r["ms"])[:10],
        "requests": slow_network,
    }


def lifecycle(entries):
    counts = Counter()
    for _, category, message in entries:
        if category == "lifecycle":
            if message.endswith(" launched"):
                counts["launches"] += 1
            elif message.startswith("previous run ended unexpectedly"):
                counts["unexpected ends"] += 1
            elif message in ("foreground", "background"):
                counts[message] += 1
    away = numbers(entries, r"^new connections after (\d+) s away")
    failures = Counter(
        m.split("refresh failed: ", 1)[1][:80]
        for _, c, m in entries
        if c == "sync" and m.startswith("refresh failed: ")
    )
    return {
        **dict(counts),
        "returns after a break": len(away),
        "refreshes": sum(
            1
            for _, c, m in entries
            if c == "sync" and m.startswith("refreshed library")
        ),
        "refresh_ms": spread(numbers(entries, r"^refreshed library in (\d+) ms")),
        "refresh_failures": dict(failures),
        "audio_interruptions": sum(
            1 for _, c, m in entries if c == "audio" and m == "interruption began"
        ),
    }


def watch(phone, watch_log):
    revisions = {}
    for stamp, category, message in phone:
        match = re.match(r"Watch confirmed revision (\d+): (\d+) songs ready", message)
        if category == "watch" and match:
            revision, ready = int(match.group(1)), int(match.group(2))
            entry = revisions.setdefault(
                revision, {"first": stamp, "last": stamp, "ready": [ready]}
            )
            entry["last"] = stamp
            entry["ready"].append(ready)
    delivery = []
    for revision, entry in sorted(revisions.items()):
        minutes = (entry["last"] - entry["first"]).total_seconds() / 60
        gained = max(entry["ready"]) - min(entry["ready"])
        delivery.append(
            {
                "revision": revision,
                "songs_ready": max(entry["ready"]),
                "over_minutes": round(minutes, 1),
                "songs_per_minute": round(gained / minutes, 2) if minutes > 0 else None,
            }
        )
    reachable = [m for _, c, m in phone if c == "link" and m.startswith("reachable=")]
    flips = sum(1 for a, b in zip(reachable, reachable[1:]) if a != b)
    # The same context applied again and again in a moment: a sync loop.
    repeats = []
    seen = defaultdict(list)
    for stamp, category, message in watch_log:
        match = re.match(r"context revision (\d+)", message)
        if category == "sync" and match:
            seen[match.group(1)].append(stamp)
    for revision, stamps in seen.items():
        quick = [b for a, b in zip(stamps, stamps[1:]) if b - a < timedelta(seconds=1)]
        if quick:
            repeats.append(
                {
                    "revision": int(revision),
                    "applied": len(stamps),
                    "within_a_second": len(quick) + 1,
                }
            )
    return {
        "delivery": delivery,
        "transfers_finished": sum(
            1 for _, c, m in phone if c == "link" and m == "file transfer finished"
        ),
        "reachability_changes": flips,
        "watch_logs_received": sum(
            1
            for _, c, m in phone
            if c == "watch" and m.startswith("Watch log received")
        ),
        "watch_reports_unexpected_end": sum(
            1
            for _, c, m in phone
            if c == "watch" and "previous run ended unexpectedly" in m
        ),
        "context_for_another_account": sum(
            1
            for _, c, m in watch_log + phone
            if c == "sync" and "for another account" in m
        ),
        "context_repeats": repeats,
        "watch_launches": sum(
            1 for _, c, m in watch_log if c == "lifecycle" and m.endswith(" launched")
        ),
    }


def findings(report):
    """Plain statements of what looks wrong, most serious first."""
    found = []
    phone = report["iphone"]
    play = phone["playback"]
    failed = Counter(
        r["outcome"]
        for r in play["requests"]
        if not r["outcome"].isdigit() or int(r["outcome"]) >= 400
    )
    if failed:
        kinds = ", ".join(f"{n}× {outcome}" for outcome, n in failed.most_common())
        found.append(
            f"Requests failed: {kinds} (their IDs are in the slowest requests)."
        )
    for kind, values in play["time_to_sound_ms"].items():
        if values and values["max"] >= 5000:
            found.append(
                f"A {kind} took {values['max'] / 1000:.1f} s to make sound (median {values['median']} ms, {values['count']} times)."
            )
    if play["address_ms"] and play["address_ms"]["max"] >= 5000:
        found.append(
            f"A song's playback address took {play['address_ms']['max'] / 1000:.1f} s (median {play['address_ms']['median']} ms): match its request ID in the server log."
        )
    for burst in play["passed_over_bursts"]:
        found.append(
            f"{burst['songs']} songs were passed over within seconds at {burst['at']}: a queue emptied itself."
        )
    if play["streaming_instead"]:
        reasons = ", ".join(f"{n}× {r}" for r, n in play["streaming_instead"].items())
        found.append(f"Seamless playback gave up for streaming: {reasons}.")
    refresh = phone["lifecycle"]["refresh_ms"]
    if refresh and refresh["max"] >= 10000:
        found.append(
            f"A library refresh took {refresh['max'] / 1000:.0f} s (median {refresh['median']} ms, p90 {refresh['p90']} ms)."
        )
    for failure, n in phone["lifecycle"]["refresh_failures"].items():
        found.append(f"{n} library refreshes failed: {failure}")
    if phone["lifecycle"].get("unexpected ends"):
        found.append(
            f"The iPhone app ended unexpectedly {phone['lifecycle']['unexpected ends']} times."
        )
    w = report["watch"]
    for entry in w["delivery"]:
        if entry["songs_per_minute"] is not None and entry["songs_per_minute"] < 2:
            found.append(
                f"Watch revision {entry['revision']}: {entry['songs_ready']} songs ready over {entry['over_minutes']} min ({entry['songs_per_minute']} songs a minute)."
            )
    for repeat in w["context_repeats"]:
        found.append(
            f"The Watch applied context revision {repeat['revision']} {repeat['within_a_second']} times within a second."
        )
    if w["context_for_another_account"]:
        found.append(
            f"A context 'for another account' arrived {w['context_for_another_account']} times: the Watch's stored account did not match."
        )
    if w["watch_reports_unexpected_end"]:
        found.append(
            f"The Watch reported {w['watch_reports_unexpected_end']} unexpected ends of its previous run."
        )
    for crash in report["crashes"]:
        summary = crash["summary"]
        found.append(
            f"Crash report {crash['name']}: {', '.join(f'{k} {v}' for k, v in summary.items()) or 'unreadable'}."
        )
    return found


def analyse(text, since=None):
    parsed = parse(text)
    devices = {
        name: [e for e in entries if since is None or e[0] >= since]
        for name, entries in parsed["devices"].items()
    }
    phone, watch_log = devices.get("iPhone", []), devices.get("Watch", [])
    span = [e[0] for e in phone + watch_log]
    report = {
        "header": parsed["header"],
        "from": min(span).isoformat(timespec="seconds") if span else None,
        "to": max(span).isoformat(timespec="seconds") if span else None,
        "iphone": {
            "lines": len(phone),
            "playback": playback(phone),
            "lifecycle": lifecycle(phone),
        },
        "watch": {
            **watch(phone, watch_log),
            "log_lines": len(watch_log),
            "log_sent": parsed["watch_sent"],
        },
        "crashes": parsed["crashes"],
        "malformed_lines": parsed["malformed_lines"],
    }
    report["findings"] = findings(report)
    if parsed["malformed_lines"]:
        report["findings"].append(
            f"{parsed['malformed_lines']} lines had an impossible time and were left out."
        )
    return report


def text_report(report):
    lines = [*report["header"], f"Covers {report['from']} to {report['to']}", ""]
    lines.append("Findings:")
    lines += [f"  - {f}" for f in report["findings"]] or ["  (none)"]
    play = report["iphone"]["playback"]
    lines += ["", "Time to sound (ms):"]
    for kind, values in play["time_to_sound_ms"].items():
        lines.append(f"  {kind:22} {values}")
    lines.append(f"  {'address':22} {play['address_ms']}")
    lines.append(f"  {'source answered':22} {play['source_answered_ms']}")
    if play["slowest_requests"]:
        lines += ["", "Slowest requests (match the ID in the server log):"]
        for r in play["slowest_requests"]:
            lines.append(
                f"  {r['ms']:7} ms  {r['operation']} {r['outcome']}  {r['server'] or ''}  id {r['id']}  {r['at']}"
            )
    lines += ["", f"iPhone: {report['iphone']['lifecycle']}"]
    w = report["watch"]
    lines += ["", "Watch:"]
    for key in (
        "transfers_finished",
        "reachability_changes",
        "watch_launches",
        "watch_logs_received",
        "context_for_another_account",
        "log_sent",
    ):
        lines.append(f"  {key.replace('_', ' ')}: {w[key]}")
    for entry in w["delivery"]:
        lines.append(
            f"  revision {entry['revision']}: {entry['songs_ready']} songs over {entry['over_minutes']} min ({entry['songs_per_minute']} a minute)"
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("export", type=Path)
    parser.add_argument("--since", help="ISO time, e.g. 2026-10-26T08:00+11:00")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    since = None
    if args.since:
        since = datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.astimezone()
    if args.export.stat().st_size > MAX_EXPORT_BYTES:
        sys.exit(f"{args.export} is larger than any diagnostics export; not read.")
    report = analyse(args.export.read_text(errors="replace"), since)
    if args.json:
        json.dump(report, sys.stdout, indent=2, default=str)
        print()
    else:
        print(text_report(report))


if __name__ == "__main__":
    main()
