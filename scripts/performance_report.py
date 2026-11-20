"""Print the parity targets (#191) and the numbers against them, from a copy
of the server's `performance.sqlite3` (the data folder keeps a rolling week).

Copy it off the server first and read the copy, never the live file:

    .venv/bin/python -m scripts.performance_report performance.sqlite3
    .venv/bin/python -m scripts.performance_report copy.sqlite3 --since 2026-11-14 --json
    .venv/bin/python -m scripts.performance_report copy.sqlite3 --no-match

`--no-match` breaks each instant source's "no match" lookups down by why
its nearest result was not the song (#282), with the newest examples.

Library artists with a photo are measured by the server (Admin, System); this
script reports them only when given `--artists WITH,TOTAL`.
"""

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from server.performance import RETENTION_SECONDS, report
from server.remote import REASONS


def epoch(text):
    return datetime.fromisoformat(text).timestamp()


def percent(value):
    return "–" if value is None else f"{value * 100:.1f}%"


def ms(value):
    return "–" if value is None else f"{value / 1000:.2f} s"


def mark(value, target, lower=False):
    if value is None:
        return ""
    met = value <= target if lower else value >= target
    return "  meets target" if met else "  below target"


def lines(result):
    targets = result["targets"]
    instant, sound = result["instant"], result["first_sound"]
    since = datetime.fromtimestamp(result["since"]).isoformat(timespec="minutes")
    until = datetime.fromtimestamp(result["until"]).isoformat(timespec="minutes")
    yield f"PigMusic parity numbers, {since} to {until}"
    yield ""
    yield (
        f"Instant listening: {instant['played']} of {instant['taps']} taps played "
        f"({percent(instant['played_share'])}, target "
        f"{percent(targets['taps_played'])})"
        + mark(instant["played_share"], targets["taps_played"])
    )
    yield (
        f"  no match {instant['no match']}, unavailable {instant['unavailable']}; "
        f"server answer median {ms(instant['server_answer']['median_ms'])}, "
        f"p90 {ms(instant['server_answer']['p90_ms'])}"
    )
    for source, count in sorted(instant["by_source"].items()):
        yield f"  played from {source}: {count}"
    for source, entry in instant["sources"].items():
        outcomes = ", ".join(f"{k} {v}" for k, v in sorted(entry["outcomes"].items()))
        yield f"  {source}: {outcomes}; found median {ms(entry['median_ms'])}"
    yield (
        f"First sound: {sound['count']} starts, median {ms(sound['median_ms'])} "
        f"(target {ms(targets['first_sound_median_ms'])})"
        + mark(sound["median_ms"], targets["first_sound_median_ms"], lower=True)
        + f", p90 {ms(sound['p90_ms'])} (target {ms(targets['first_sound_p90_ms'])})"
        + mark(sound["p90_ms"], targets["first_sound_p90_ms"], lower=True)
    )
    for mode, entry in sound["by_mode"].items():
        yield f"  {mode}: {entry['count']}, median {ms(entry['median_ms'])}"
    for kind, target in (
        ("mix", "mix_downloads_completed"),
        ("library", "library_downloads_completed"),
    ):
        entry = result["downloads"][kind]
        yield (
            f"Downloads ({kind}): {entry['completed']} completed, {entry['failed']} "
            f"failed, {entry['cancelled']} cancelled ({percent(entry['completion'])}, "
            f"target {percent(targets[target])})"
            + mark(entry["completion"], targets[target])
        )
        for source, count in sorted(entry["by_source"].items()):
            yield f"  {source}: {count}"
    art = result["artwork"]
    yield (
        f"Artwork: {art['placeholders']} of {art['distinct']} covers asked for "
        f"never shown ({percent(art['placeholder_share'])}, target at most "
        f"{percent(targets['artwork_placeholders'])})"
        + mark(art["placeholder_share"], targets["artwork_placeholders"], lower=True)
    )
    for kind, entry in art["kinds"].items():
        yield (
            f"  {kind}: {entry['served']} served, {entry['missing']} missing, "
            f"{entry['placeholders']} of {entry['distinct']} never shown"
        )
    for kind, counts in sorted(art.get("sources", {}).items()):
        total = sum(counts.values())
        yield f"  {kind}s fetched: " + ", ".join(
            f"{source} {count} ({percent(count / total)})"
            for source, count in sorted(counts.items(), key=lambda c: -c[1])
        )
    if result["artists"]:
        artists = result["artists"]
        yield (
            f"Library artists with a photo: {artists['with_photo']} of "
            f"{artists['total']} ({percent(artists['share'])}, target "
            f"{percent(targets['artists_with_photo'])})"
            + mark(artists["share"], targets["artists_with_photo"])
        )


def no_matches(conn, since=None, until=None):
    """Each source's "no match" lookups (#282): by the reason its nearest
    result failed the song rule (the one that got furthest through it), by
    how many lookups had any result fail for each reason, and the events, newest
    first. Events from before the detail was kept count as "unrecorded"."""
    until = time.time() if until is None else until
    since = until - RETENTION_SECONDS if since is None else since
    rows = conn.execute(
        "SELECT at,label,detail FROM events WHERE metric='remote-source' "
        "AND outcome='no match' AND at>=? AND at<? ORDER BY at DESC",
        (since, until),
    ).fetchall()
    sources = {}
    for at, label, detail in rows:
        entry = sources.setdefault(
            label, {"count": 0, "nearest": Counter(), "any": Counter(), "events": []}
        )
        entry["count"] += 1
        try:
            value = json.loads(detail) if detail else None
        except ValueError:
            value = None
        if not isinstance(value, dict):
            entry["nearest"]["unrecorded"] += 1
            continue
        top = [r for r in value.get("top") or [] if isinstance(r, dict)]
        if "error" in value:
            nearest = "error " + str(value["error"])
        elif value.get("remembered"):
            nearest = "remembered miss"
        elif not value.get("results"):
            nearest = "no results"
        else:
            every = value.get("reasons")
            every = (
                set(every)
                if isinstance(every, dict)
                else {r.get("why") for r in top if r.get("why")}
            )
            known = [reason for reason in every if reason in REASONS]
            nearest = max(known, key=REASONS.index) if known else "unrecorded"
            entry["any"].update(every)
        entry["nearest"][nearest] += 1
        entry["events"].append({"at": at, "nearest": nearest, **value})
    return {
        name: {**entry, "nearest": dict(entry["nearest"]), "any": dict(entry["any"])}
        for name, entry in sorted(sources.items())
    }


def by_count(counts):
    return ", ".join(
        f"{reason} {count}"
        for reason, count in sorted(counts.items(), key=lambda r: (-r[1], r[0]))
    )


def length(seconds):
    if not isinstance(seconds, (int, float)):
        return "no length"
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def no_match_lines(found, examples=20):
    if not found:
        yield "No instant source answered no match in this period."
    for source, entry in found.items():
        yield f"{source}: {entry['count']} no match"
        yield "  nearest result failed on: " + by_count(entry["nearest"])
        if entry["any"]:
            yield "  lookups with a result that failed on: " + by_count(entry["any"])
        for event in entry["events"][:examples]:
            when = datetime.fromtimestamp(event["at"]).isoformat(timespec="minutes")
            yield (
                f"  {when} {event.get('query', '')!r} ({length(event.get('seconds'))})"
                f": {event.get('results', 0)} results, nearest {event['nearest']}"
            )
            for result in event.get("top") or []:
                yield (
                    f"    {result.get('artist')} - {result.get('title')} "
                    f"[{result.get('album')}] ({length(result.get('seconds'))})"
                    f": {result.get('why')}"
                )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("database", type=Path, help="a copy of performance.sqlite3")
    parser.add_argument(
        "--since", type=epoch, help="ISO date or time (default: a week before --until)"
    )
    parser.add_argument("--until", type=epoch, help="ISO date or time (default: now)")
    parser.add_argument("--artists", help="WITH,TOTAL library artists with a photo")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--no-match",
        action="store_true",
        help="each instant source's no-match lookups by reason, with examples",
    )
    parser.add_argument(
        "--examples", type=int, default=20, help="examples per source (--no-match)"
    )
    args = parser.parse_args(argv)
    if not args.database.is_file():
        parser.error(f"{args.database} does not exist")
    artists = None
    if args.artists:
        with_photo, total = (int(part) for part in args.artists.split(","))
        artists = (with_photo, total)
    # Read-only: a copy is never changed, and the live file must not be used.
    uri = args.database.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        if args.no_match:
            found = no_matches(conn, args.since, args.until)
        else:
            result = report(conn, args.since, args.until, artists)
    if args.no_match:
        if args.json:
            json.dump(found, sys.stdout, indent=2, ensure_ascii=False)
            print()
        else:
            print("\n".join(no_match_lines(found, args.examples)))
    elif args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
    else:
        print("\n".join(lines(result)))


if __name__ == "__main__":
    main()
