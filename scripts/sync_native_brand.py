"""Vendor the approved PigMusic 1.7 assets; consumers need no branding checkout."""

import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sync(source):
    tokens = json.loads((source / "tokens/brand-tokens.json").read_text())
    if tokens["version"] != "1.7.0":
        raise ValueError("Review a new branding version before adopting it")
    product = source / "assets/products/pig-music"
    imported = {}

    def copy(src, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        imported[str(dst.relative_to(ROOT))] = {
            "source": str(src.relative_to(source)),
            "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        }

    copy(
        source / "tokens/brand-tokens.json", ROOT / "docs/pig-family-brand-tokens.json"
    )

    # Tokens are shared by web and native. Refresh the existing shared receipt and
    # its already-vendored assets together, without changing web layout or behavior.
    receipt_path = ROOT / "docs/brand-asset-provenance.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["version"] = tokens["version"]
    for entry in receipt["assets"]:
        src = source / entry["path"]
        payload = src.read_bytes()
        if entry["path"] == "tokens/brand-tokens.json":
            destination = ROOT / "docs/pig-family-brand-tokens.json"
        elif entry["path"] == "tokens/pig-radiance.css":
            destination = ROOT / "client/src/brand/pig-radiance.css"
        elif "/Apple/" in entry["path"]:
            destination = (
                ROOT / "swift/PigMusic/Assets.xcassets/AppIcon.appiconset" / src.name
            )
        else:
            destination = ROOT / "client/public/brand" / src.name
        shutil.copy2(src, destination)
        entry.update(
            copiedTo=str(destination.relative_to(ROOT)),
            bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        if src.suffix == ".png":
            entry["width"], entry["height"] = struct.unpack(">II", payload[16:24])
            entry["mode"] = {2: "RGB", 6: "RGBA"}[payload[25]]
        elif src.suffix == ".svg":
            entry["svgType"] = "self-contained-vector"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    def imageset(catalog, name, files, vector=False):
        folder = catalog / (name + ".imageset")
        images = []
        for index, src in enumerate(files):
            copy(src, folder / src.name)
            entry = {"filename": src.name, "idiom": "universal"}
            if index == 1:
                entry["appearances"] = [{"appearance": "luminosity", "value": "dark"}]
            images.append(entry)
        contents = {"images": images, "info": {"author": "Pig family", "version": 1}}
        if vector:
            contents["properties"] = {"preserves-vector-representation": True}
        (folder / "Contents.json").write_text(json.dumps(contents, indent=2) + "\n")

    for app in ("PigMusic", "PigMusicWatch"):
        catalog = ROOT / "swift" / app / "Assets.xcassets"
        for path in (source / "tokens/PigColors.xcassets").glob(
            "*.colorset/Contents.json"
        ):
            copy(path, catalog / path.parent.name / path.name)
        for path in (product / "Apple/Glyphs.xcassets").glob("*.imageset/*"):
            copy(path, catalog / path.parent.name / path.name)
        imageset(catalog, "PigMark", [product / "mark.svg"], True)
        # watchOS has no light appearance and shows the default image on its
        # black background, so the Watch gets only the dark-background lockup.
        lockups = ["lockup-horizontal-light.svg", "lockup-horizontal-dark.svg"]
        if app == "PigMusicWatch":
            lockups = lockups[1:]
            shutil.rmtree(catalog / "PigLockup.imageset", ignore_errors=True)
        imageset(catalog, "PigLockup", [product / name for name in lockups], True)
        imageset(
            catalog,
            "PigLaunch",
            [
                product / "Apple/AppIcon.appiconset/light.png",
                product / "Apple/AppIcon.appiconset/dark.png",
            ],
        )
        imageset(
            catalog,
            "PigHalo",
            [
                product / "Apple/IconComposer/background-light.png",
                product / "Apple/IconComposer/background-dark.png",
            ],
        )
        imageset(
            catalog,
            "PigIconBody",
            [product / "Apple/IconComposer/layer-1-body.svg"],
            True,
        )
        imageset(
            catalog,
            "PigIconFace",
            [product / "Apple/IconComposer/layer-2-face.svg"],
            True,
        )
    phone = ROOT / "swift/PigMusic/Assets.xcassets/AppIcon.appiconset"
    for path in (product / "Apple/AppIcon.appiconset").iterdir():
        copy(path, phone / path.name)
    # watchOS 11 fallback is the approved unmasked square; the system applies its circle.
    copy(
        product / "Apple/AppIcon.appiconset/dark.png",
        ROOT / "swift/PigMusicWatch/Assets.xcassets/AppIcon.appiconset/icon.png",
    )
    icon = ROOT / "swift/PigMusic/AppIcon.icon/Assets"
    for src, filename in [
        ("layer-1-body.svg", "body.svg"),
        ("layer-2-face.svg", "face.svg"),
        ("mono/layer-1-body.svg", "body-mono.svg"),
        ("mono/layer-2-face.svg", "face-mono.svg"),
        ("background-light.png", "halo-light.png"),
        ("background-dark.png", "halo-dark.png"),
    ]:
        copy(product / "Apple/IconComposer" / src, icon / filename)
    (ROOT / "docs/native-brand-assets.json").write_text(
        json.dumps(
            {"version": tokens["version"], "assets": imported}, indent=2, sort_keys=True
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        manifest = json.loads((ROOT / "docs/native-brand-assets.json").read_text())
        for name, entry in manifest["assets"].items():
            path = ROOT / name
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]
            ):
                raise SystemExit("Approved brand asset drift: " + name)
        print(f"Verified {len(manifest['assets'])} approved brand files")
    elif args.source:
        sync(args.source.resolve())
    else:
        parser.error(
            "Provide --source to import the approved kit, or --check to verify it"
        )
