"""Search-only coverage test for every acquisition source; nothing is downloaded.

Run as a module from the repository root. Targets are the current Apple Music
most-played songs for a region plus the curated scripts/coverage_sample.json,
resolved through the public iTunes catalogue for their album and duration. An
optional --tracks file adds more recordings (artist, title, album, duration).

Each source is searched with the same matching rules as acquisition. A fresh
state directory is created under --output; no appdata, mounts, source settings
or cookies are read. Run it from the Unraid host for representative results.
"""

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from server.downloader import VERSION_WORDS, folded, matches
from server.providers import REGISTRY
from server.providers.contracts import Context, ProviderError
from server.providers.legacy import search as legacy_search
from server.providers.runtime import Runtime
from server.store import Store

SOURCES = ("soundcloud", "monochrome", "jiosaavn", "freemp3cloud", "zvu4no")
# Paces sources that throttle or silently return empty pages when searched fast.
GAPS = {"freemp3cloud": 2.5, "zvu4no": 1.0, "jiosaavn": 0.5}
CHART_URL = (
    "https://rss.marketingtools.apple.com/api/v2/{}/music/most-played/{}/songs.json"
)
ITUNES = "https://itunes.apple.com/"
HEADERS = {"User-Agent": "PigMusic/1.0 (https://github.com/maroge1990/Pig-Music)"}
SAMPLE = Path(__file__).with_name("coverage_sample.json")


def itunes_target(item, kind):
    return {
        "kind": kind,
        "artist": item["artistName"],
        "album_artist": item.get("collectionArtistName") or item["artistName"],
        "title": item["trackName"],
        # Catalogue requests name singles and EPs without Apple's suffix.
        "album": re.sub(r"\s+-\s+(Single|EP)$", "", item.get("collectionName", "")),
        "duration": round(item.get("trackTimeMillis", 0) / 1000, 1),
    }


def chart_targets(client, region, size):
    feed = client.get(CHART_URL.format(region, size), headers=HEADERS, timeout=10)
    feed.raise_for_status()
    ids = [r["id"] for r in feed.json()["feed"]["results"] if r.get("id")]
    found = {}
    for start in range(0, len(ids), 150):
        lookup = client.get(
            ITUNES + "lookup",
            params={"id": ",".join(ids[start : start + 150]), "country": region},
            headers=HEADERS,
            timeout=15,
        )
        lookup.raise_for_status()
        for item in lookup.json()["results"]:
            if item.get("wrapperType") == "track":
                found[str(item["trackId"])] = item
    return [
        {**itunes_target(found[i], "apple_chart"), "chart_position": rank}
        for rank, i in enumerate(ids, 1)
        if i in found
    ]


def curated_target(client, region, entry):
    # The public iTunes search allows roughly 20 requests a minute.
    for wait in (60, 120, None):
        response = client.get(
            ITUNES + "search",
            params={
                "term": f"{entry['artist']} {entry['title']}",
                "country": region,
                "entity": "song",
                "limit": 25,
            },
            headers=HEADERS,
            timeout=15,
        )
        if response.status_code != 429 or wait is None:
            break
        time.sleep(wait)
    response.raise_for_status()
    for item in response.json()["results"]:
        if folded(entry["artist"]) not in folded(item.get("artistName")):
            continue
        # Apple titles add credits and editions; the requested version must remain.
        if base_title(item.get("trackName")) != base_title(entry["title"]):
            continue
        found = {
            "title": item.get("trackName", ""),
            "album": item.get("collectionName"),
        }
        if not versions({"title": entry["title"]}) <= versions(found):
            continue
        if entry.get("album") and not folded(item.get("collectionName")).startswith(
            folded(entry["album"])
        ):
            continue
        return itunes_target(item, entry.get("kind", "curated"))
    return None


def base_title(value):
    value = re.sub(r"[\(\[].*?[\)\]]", " ", value or "")
    value = re.split(r"\s+-\s+|\s+feat\.?\s+|\s+ft\.?\s+", value, flags=re.I)[0]
    return folded(value)


def versions(candidate):
    text = folded(f"{candidate.get('title') or ''} {candidate.get('album') or ''}")
    return set(text.split()) & VERSION_WORDS


def near(target, candidate):
    """Same song by the same artist, whatever its version, album or length."""
    artist, channel = folded(target["artist"]), folded(candidate.get("channel"))
    first = folded(re.split(r",|&| x | and ", target["artist"], flags=re.I)[0])
    same_artist = channel and (channel in artist or (first and first in channel))
    return bool(same_artist) and base_title(candidate.get("title")) == base_title(
        target["title"]
    )


def classify(target, candidates):
    confident = [c for c in candidates if matches(target, c)]
    if confident:
        best = confident[0]
        return "confident", {
            "quality": best.get("quality") or best.get("extension") or "",
            "candidate": {k: best.get(k) for k in ("channel", "title", "album")},
        }
    close = [c for c in candidates if near(target, c)]
    expected = versions(target)
    # The right version failing a check is "near", even beside other versions.
    if any(versions(c) == expected for c in close):
        return "near_only", {}
    if close:
        return "wrong_version_only", {}
    return "none", {}


def search(runtime, source, target):
    profile = "lossless" if source == "monochrome" else "native_standard"
    context = Context(profile, deadline=time.monotonic() + 90)
    if REGISTRY[source].adapter:
        return runtime.search(source, target, context)
    return legacy_search(source, f"{target['artist']} {target['title']}", context)


def run_source(runtime, source, targets):
    rows = []
    for target in targets:
        started = time.monotonic()
        try:
            outcome, detail = classify(target, search(runtime, source, target))
        except ProviderError as exc:
            outcome, detail = "error", {"error": exc.code}
        except Exception as exc:  # A broken parser must not end the run.
            outcome, detail = "error", {"error": type(exc).__name__}
        rows.append(
            {
                "source": source,
                "artist": target["artist"],
                "title": target["title"],
                "kind": target["kind"],
                "outcome": outcome,
                "seconds": round(time.monotonic() - started, 2),
                **detail,
            }
        )
        time.sleep(GAPS.get(source, 0))
    return rows


def summarise(sources, targets, rows):
    kinds = sorted({t["kind"] for t in targets})
    lines = [
        f"# Source coverage, {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"{len(targets)} recordings. Confident = passes acquisition matching. "
        "Wrong version = only a live, remix, remaster or other version was found. "
        "Near = the song was found but failed album, length or credit checks.",
        "",
        "| Source | Confident | Wrong version | Near | None | Errors | Median search |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for source in sources:
        mine = [r for r in rows if r["source"] == source]
        count = {o: sum(r["outcome"] == o for r in mine) for o in OUTCOMES}
        times = sorted(r["seconds"] for r in mine if r["outcome"] != "error")
        median = f"{times[len(times) // 2]:.1f}s" if times else "-"
        lines.append(
            f"| {source} | "
            + " | ".join(
                f"{count[o]} ({100 * count[o] // max(1, len(mine))}%)" for o in OUTCOMES
            )
            + f" | {median} |"
        )
    lines += ["", "## Confident matches by kind", ""]
    lines.append("| Kind | Recordings | " + " | ".join(sources) + " | Any source |")
    lines.append("| --- " * (len(sources) + 3) + "|")
    for kind in kinds + ["all"]:
        chosen = [t for t in targets if kind in ("all", t["kind"])]
        keys = {(t["artist"], t["title"]) for t in chosen}
        hits = {
            s: {
                (r["artist"], r["title"])
                for r in rows
                if r["source"] == s
                and r["outcome"] == "confident"
                and (r["artist"], r["title"]) in keys
            }
            for s in sources
        }
        anywhere = set().union(*hits.values())
        lines.append(
            f"| {kind} | {len(keys)} | "
            + " | ".join(str(len(hits[s])) for s in sources)
            + f" | {len(anywhere)} |"
        )
    return "\n".join(lines) + "\n"


OUTCOMES = ("confident", "wrong_version_only", "near_only", "none", "error")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New isolated directory; existing directories are refused",
    )
    parser.add_argument("--region", default="au")
    parser.add_argument(
        "--chart-size", type=int, default=100, choices=(10, 25, 50, 100)
    )
    parser.add_argument("--sources", default=",".join(SOURCES))
    parser.add_argument("--tracks", type=Path, help="Extra recordings as a JSON list")
    parser.add_argument("--limit", type=int, help="Use only the first N recordings")
    args = parser.parse_args()
    sources = [s for s in args.sources.split(",") if s]
    unknown = set(sources) - set(SOURCES)
    if unknown:
        parser.error("Unknown sources: " + ", ".join(sorted(unknown)))
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)

    with httpx.Client(follow_redirects=False) as client:
        targets = chart_targets(client, args.region, args.chart_size)
        unresolved = []
        for entry in json.loads(SAMPLE.read_text())["tracks"]:
            target = curated_target(client, args.region, entry)
            if target:
                targets.append(target)
            else:
                unresolved.append(entry)
            time.sleep(3.2)
    if args.tracks:
        for item in json.loads(args.tracks.read_text()):
            targets.append(
                {"kind": "household", "album_artist": item["artist"], **item}
            )
    seen, unique = set(), []
    for target in targets:
        key = (folded(target["artist"]), folded(target["title"]))
        if key not in seen:
            seen.add(key)
            unique.append(target)
    targets = unique[: args.limit] if args.limit else unique
    print(f"{len(targets)} recordings, {len(unresolved)} curated entries unresolved")

    runtime = Runtime(Store(output / "state"))
    configured = [s for s in sources if runtime.configured(s)]
    for source in set(sources) - set(configured):
        print(f"Skipping {source}: runtime not configured")
    with ThreadPoolExecutor(len(configured) or 1) as pool:
        results = pool.map(lambda s: run_source(runtime, s, targets), configured)
        rows = [row for result in results for row in result]

    (output / "targets.json").write_text(json.dumps(targets, indent=2) + "\n")
    (output / "unresolved.json").write_text(json.dumps(unresolved, indent=2) + "\n")
    (output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    summary = summarise(configured, targets, rows)
    (output / "summary.md").write_text(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
