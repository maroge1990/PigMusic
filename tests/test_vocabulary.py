"""One word and one icon for each thing on web, iPhone and Watch (#246).

D-115 named asking the server for music "Add to library", D-116 chose one
icon per meaning and D-117 one word per thing; docs/DESIGN.md holds the
table. This test reads the text the apps show (string literals in the web
client and the Swift sources) and the icons they draw, and fails when a
retired word or icon comes back."""

import re
import sys

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WEB = [ROOT / "client" / "src"]
SWIFT = [
    ROOT / "swift" / "PigMusic",
    ROOT / "swift" / "PigMusicWatch",
    ROOT / "swift" / "PigMusicShare",
    ROOT / "swift" / "PigKit" / "Sources",
]

# Retired words, matched in text people see.
RETIRED_WORDS = {
    r"\brequest(s|ed|ing)?\b": 'asking for music is "Add to library", "Adding" (D-115)',
    r"\bdownload all missing\b": '"Add N songs to library" (D-115)',
    r"\bmy library\b": '"your library" (D-117)',
    r"\bretry\b": '"Try again" (D-117)',
    r"\bsync(ing|ed)?\b": '"Refresh" (D-117)',
    r"\btracks?\b": '"songs" (D-117)',
    r"\breleases?\b": '"albums" (D-117)',
    r"\bbudget\b": '"Storage limit" (D-117)',
    r"\bunable\b": '"Couldn\'t …" (D-117)',
    r"\bcould not\b": '"Couldn\'t …" (D-117)',
    r"\bfailed\b": '"Couldn\'t …" (D-117)',
    r"\bphone\b": '"iPhone" (D-117)',
    r"(?<!apple )\bwatch\b": '"Apple Watch" (D-117)',
    r"\bwaiting for transfer\b": '"Waiting for Apple Watch" (D-117)',
}

# Retired icons: SF Symbols (Swift) and lucide components (web).
RETIRED_SYMBOLS = {
    "cloud": "no mark: on the server, streams (D-116)",
    "cloud.fill": "no mark (D-116)",
    "hourglass": "clock for waiting (D-116)",
    "exclamationmark.arrow.circlepath": "exclamationmark.circle for failed (D-116)",
    "arrow.counterclockwise": "clock.arrow.circlepath for Rewind (D-116)",
    "ellipsis.circle": "ellipsis for more actions (D-116)",
    "arrow.down.circle.dotted": "arrow.down.circle for downloads (D-116)",
}
RETIRED_LUCIDE = {
    "Heart": "Star for favourite (D-116)",
    "Cloud": "no mark (D-116)",
    "Download": "Plus for Add to library (D-116)",
    "ArrowDownToLine": "CircleArrowDown for Downloads (D-116)",
}

# Strings that look like text but are not shown as words (keep this short,
# with a reason for each).
ALLOWED = {
    # Brand names and system terms.
    "PigSync",
}

STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
SYMBOL = re.compile(r'(?:systemName|systemImage)\s*:\s*"([a-z0-9.]+)"')
LUCIDE = re.compile(r"import\s*\{([^}]*)\}\s*from\s*\"lucide-react\"", re.S)


def sources(roots, suffixes):
    for root in roots:
        for path in sorted(root.rglob("*")):
            developer_tools = {"Tests", "PigAudioProbe", "PigPlaybackHarness"}
            if path.suffix in suffixes and not developer_tools & set(path.parts):
                yield path


def shown_text(line):
    """String literals on a line that read as words a person sees."""
    stripped = line.strip()
    if stripped.startswith(("//", "*", "/*", "import ", "#")):
        return
    if re.search(r"\b(log(ger)?|Logger|os_log|print|console)\b|className", line):
        return  # diagnostics and style names, not words on screen
    for match in STRING.finditer(line):
        text = re.sub(r"\\\(.*?\)|\$\{.*?\}", " ", match.group(1))
        if text in ALLOWED:
            continue
        if " " in text.strip() or re.fullmatch(r"[A-Z][a-z]+", text):
            if re.match(
                r"\s*(SELECT|INSERT|CREATE|ALTER|UPDATE|DELETE|PRAGMA|WITH)\b", text
            ):
                continue  # SQL
            if not re.match(r"\s*[A-Z]", text):
                continue  # diagnostics and keys start in lower case
            if not re.search(r"[/=<>{}]|https?:|\.\w+$", text):
                yield text


def findings(roots=None):
    found = []
    for path in sources(roots or WEB + SWIFT, {".swift", ".ts", ".tsx"}):
        rel = path.relative_to(ROOT)
        for number, line in enumerate(path.read_text().splitlines(), 1):
            for text in shown_text(line):
                for pattern, instead in RETIRED_WORDS.items():
                    if re.search(pattern, text, re.I):
                        found.append(f"{rel}:{number} {text!r}: use {instead}")
            if path.suffix == ".swift":
                for symbol in SYMBOL.findall(line):
                    if symbol in RETIRED_SYMBOLS:
                        found.append(
                            f"{rel}:{number} {symbol}: {RETIRED_SYMBOLS[symbol]}"
                        )
        if path.suffix == ".tsx" or path.suffix == ".ts":
            for block in LUCIDE.findall(path.read_text()):
                for name in (n.strip().split(" as ")[0] for n in block.split(",")):
                    if name in RETIRED_LUCIDE:
                        found.append(f"{rel}: {name}: {RETIRED_LUCIDE[name]}")
    return found


@pytest.mark.parametrize("platform", ["web", "swift"])
def test_the_apps_use_one_word_and_one_icon_for_each_thing(platform):
    found = findings({"web": WEB, "swift": SWIFT}[platform])
    assert not found, f"{len(found)} retired words or icons:\n" + "\n".join(found)


def test_the_check_sees_retired_words_and_icons(tmp_path, monkeypatch):
    web = tmp_path / "web"
    web.mkdir()
    (web / "A.tsx").write_text(
        'import { Heart, Play } from "lucide-react";\n'
        'const a = "Request song";\n'
        'const b = "./PersonalRequests";\n'
        'const c = "Send to Apple Watch";\n'
    )
    swift = tmp_path / "swift"
    swift.mkdir()
    (swift / "B.swift").write_text(
        'Label("Retry", systemImage: "cloud")\n'
        '// "Request song" in a comment\n'
        'Text("On your watch")\n'
    )
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "WEB", [web])
    monkeypatch.setattr(sys.modules[__name__], "SWIFT", [swift])
    text = "\n".join(findings())
    assert "'Request song'" in text and "Heart" in text
    assert "'Retry'" in text and " cloud:" in text and "'On your watch'" in text
    assert "PersonalRequests" not in text and "Apple Watch'" not in text
    assert text.count("\n") == 4
