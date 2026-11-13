"""Print the parity targets (#191) and the numbers against them, from a copy
of the server's `performance.sqlite3` (the data folder keeps a rolling week).

Copy it off the server first and read the copy, never the live file:

    .venv/bin/python -m scripts.performance_report performance.sqlite3
    .venv/bin/python -m scripts.performance_report copy.sqlite3 --since 2026-11-14 --json

Library artists with a photo are measured by the server (Admin, System); this
script reports them only when given `--artists WITH,TOTAL`.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from server.performance import report


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
    if result["artists"]:
        artists = result["artists"]
        yield (
            f"Library artists with a photo: {artists['with_photo']} of "
            f"{artists['total']} ({percent(artists['share'])}, target "
            f"{percent(targets['artists_with_photo'])})"
            + mark(artists["share"], targets["artists_with_photo"])
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
        result = report(conn, args.since, args.until, artists)
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
    else:
        print("\n".join(lines(result)))


if __name__ == "__main__":
    main()
