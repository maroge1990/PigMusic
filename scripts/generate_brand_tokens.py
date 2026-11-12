"""Generate deterministic web/Swift roles from the approved branding source."""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def kebab(value):
    return re.sub(r"([A-Z])", r"-\1", value).lower()


# One type scale for web and Apple clients. Web sizes are rem so browser text
# settings apply; Swift uses the matching Dynamic Type style.
TYPE_ROLES = [
    ("caption", 12, "caption"),
    ("footnote", 13, "footnote"),
    ("subhead", 15, "subheadline"),
    ("body", 16, "body"),
    ("headline", 20, "title3"),
    ("title", 28, "title"),
    ("display", 36, "largeTitle"),
]

# Content artwork stays appearance-independent. These are PigBrand roles,
# not an additional palette; action/focus colours remain semantic theme roles.
MIX_ARTWORK = {
    "daily": (("logo", "brown"), ("logo", "pink")),
    "artist": (("logo", "pink"), ("logo", "brown")),
    "genre": (("dark", "canvas"), ("logo", "rose")),
    "popular": (("logo", "coral"), ("logo", "brown")),
    "back": (("light", "raised"), ("light", "accent")),
    # The six newer kinds: option B "bold", chosen by the owner on 21 Oct.
    "radio": (("light", "accent"), ("logo", "white")),
    "fresh": (("logo", "innerEar"), ("logo", "brown")),
    "releases": (("logo", "brownSecondary"), ("logo", "innerEar")),
    "decade": (("logo", "coral"), ("logo", "white")),
    "deep": (("dark", "canvas"), ("logo", "pink")),
    "recent": (("logo", "white"), ("logo", "brown")),
}


def outputs():
    tokens = json.loads((ROOT / "docs/pig-family-brand-tokens.json").read_text())
    css = [f"/* Generated from Pig family {tokens['version']}; do not edit. */"]
    for theme in ("light", "dark"):
        selector = (
            ':root,\n:root[data-theme="light"]'
            if theme == "light"
            else ':root[data-theme="dark"]'
        )
        css += [selector + " {", f"  color-scheme: {theme};"]
        css += [
            f"  --{kebab(key)}: {value.lower()};"
            for key, value in tokens[theme].items()
        ]
        css += ["}"]
    css += [":root {"]
    for kind, pair in MIX_ARTWORK.items():
        for name, (section, role) in zip(("background", "foreground"), pair):
            css += [f"  --mix-{kind}-{name}: {tokens[section][role].lower()};"]
    for name, value in tokens["radii"]["web"].items():
        css += [f"  --radius-{name}: {value / 16:g}rem;"]
    css += ["  --radius-hero: 1.375rem;", "  --radius-capsule: 999px;"]
    for size in tokens["spacing"]:
        css += [f"  --space-{size}: {size / 16:g}rem;"]
    for name, size, _ in TYPE_ROLES:
        css += [f"  --type-{name}: {size / 16:g}rem;"]
    for key, value in tokens["media"].items():
        if isinstance(value, str) and value.startswith("#"):
            css += [f"  --media-{kebab(key)}: {value.lower()};"]
    css += [f"  --media-raised: {tokens['dark']['raised'].lower()};"]
    # Existing shadow/scrim opacities, derived from approved media black.
    for name, alpha in (
        ("shadow-soft", 4),
        ("shadow-card", 5),
        ("shadow-floating", 7),
        ("shadow-dialog", 10),
        ("shadow-dropdown", 3),
        ("scrim", 11),
    ):
        percent = f"{alpha / 15 * 100:.6f}".rstrip("0").rstrip(".")
        line = f"  --{name}: color-mix(in srgb, var(--media-canvas) {percent}%, transparent);"
        if len(line) > 80:
            css += [
                f"  --{name}: color-mix(",
                "    in srgb,",
                f"    var(--media-canvas) {percent}%,",
                "    transparent",
                "  );",
            ]
        else:
            css += [line]
    # The kit's own stylesheets (pig-glass.css) read --pig-* names.
    css += [f"  --pig-{kebab(key)}: var(--{kebab(key)});" for key in tokens["light"]]
    css += [
        "  --selected-fill: color-mix(in srgb, var(--accent) 14%, var(--surface));",
        "  --success-fill: color-mix(in srgb, var(--success) 14%, var(--surface));",
        "  --warning-fill: color-mix(in srgb, var(--warning) 14%, var(--surface));",
        "  --danger-fill: color-mix(in srgb, var(--danger) 14%, var(--surface));",
        f"  --media-secondary: {tokens['dark']['secondary'].lower()};",
        f"  --media-control-border: {tokens['dark']['controlBorder'].lower()};",
        f"  --media-border: {tokens['dark']['border'].lower()};",
        "}",
    ]
    swift = [
        f"// Generated from Pig family {tokens['version']}; do not edit.",
        "import SwiftUI",
        "",
        "public enum PigBrand {",
    ]
    # Both platforms resolve appearance in their views, with no UIKit dependency on Watch.
    for key in tokens["light"]:
        swift += [f"    public static func {key}(_ scheme: ColorScheme) -> Color {{"]
        rgb = []
        for theme in ("light", "dark"):
            h = tokens[theme][key].lstrip("#")
            rgb.append(
                ", ".join(
                    f"{name}: {int(h[i:i+2],16) / 255:.8f}"
                    for name, i in [("red", 0), ("green", 2), ("blue", 4)]
                )
            )
        swift += [
            f"        scheme == .dark ? Color({rgb[1]}) : Color({rgb[0]})",
            "    }",
        ]
    swift += [
        "}",
        "",
        "public enum PigMixArtwork {",
        "    public static func colours(_ kind: String) -> (background: Color, foreground: Color) {",
        "        switch kind {",
    ]
    for kind, pair in MIX_ARTWORK.items():
        colours = []
        for section, role in pair:
            h = tokens[section][role].lstrip("#")
            rgb = ", ".join(
                f"{name}: {int(h[i:i+2],16) / 255:.8f}"
                for name, i in [("red", 0), ("green", 2), ("blue", 4)]
            )
            colours.append(f"Color({rgb})")
        swift += [f'        case "{kind}": return ({", ".join(colours)})']
    swift += [
        '        default: return colours("daily")',
        "        }",
        "    }",
        "}",
        "",
        "public enum PigType {",
    ]
    for name, _, style in TYPE_ROLES:
        swift += [f"    public static let {name}: Font = .{style}"]
    swift += ["}"]
    return {
        ROOT / "client/src/tokens.css": "\n".join(css) + "\n",
        ROOT / "swift/PigKit/Sources/PigBrand/Tokens.swift": "\n".join(swift) + "\n",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, content in outputs().items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    if stale:
        parser.exit(1, "Stale brand output: " + ", ".join(stale) + "\n")


if __name__ == "__main__":
    main()
