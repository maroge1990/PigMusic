"""Approved identity delivery and source-of-truth drift gates."""

import hashlib
import json
import subprocess
import sys

from fastapi.testclient import TestClient

from server.app import ROOT, create_app
import pytest

from source import SOURCE


def test_brand_outputs_are_current():
    root = ROOT
    subprocess.run(
        [sys.executable, str(root / "scripts/generate_brand_tokens.py"), "--check"],
        check=True,
    )


def test_production_identity_routes_are_images_not_html(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "brand").mkdir()
    (dist / "brand/icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (dist / "index.html").write_text("<title>PigMusic</title>")
    (dist / "manifest.webmanifest").write_text(
        json.dumps({"scope": "/", "start_url": "/"})
    )
    with TestClient(create_app(tmp_path / "state", dist)) as client:
        image = client.get("/brand/icon.png")
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        manifest = client.get("/manifest.webmanifest")
        assert manifest.headers["content-type"] == "application/manifest+json"
        assert manifest.json()["scope"] == "/"
        assert manifest.headers["cache-control"] == "no-cache"
        assert client.get("/brand/missing.png").status_code == 404
        assert client.get("/brand/%2e%2e/index.html").status_code == 404


def test_watch_lockup_is_the_dark_background_one():
    """watchOS has no light appearance: it shows an image set's default (#112)."""
    folder = SOURCE / "swift/PigMusicWatch/Assets.xcassets/PigLockup.imageset"
    if not folder.is_dir():
        pytest.skip("the Watch app is not part of this checkout")
    images = json.loads((folder / "Contents.json").read_text())["images"]
    assert images == [{"filename": "lockup-horizontal-dark.svg", "idiom": "universal"}]
    assert sorted(p.name for p in folder.iterdir()) == [
        "Contents.json",
        "lockup-horizontal-dark.svg",
    ]


def test_vendored_radiance_matches_approved_release():
    provenance = json.loads((SOURCE / "docs/brand-asset-provenance.json").read_text())
    tokens = json.loads((SOURCE / "docs/pig-family-brand-tokens.json").read_text())
    assert provenance["version"] == tokens["version"]
    for asset in provenance["assets"]:
        copied = (SOURCE / asset["copiedTo"]).read_bytes()
        assert len(copied) == asset["bytes"], asset["path"]
        assert hashlib.sha256(copied).hexdigest() == asset["sha256"], asset["path"]
    radiance = next(
        a for a in provenance["assets"] if a["path"] == "tokens/pig-radiance.css"
    )
    vendored = (SOURCE / radiance["copiedTo"]).read_bytes()
    assert hashlib.sha256(vendored).hexdigest() == radiance["sha256"]


def test_vendored_brand_is_1_9_with_the_glass_stylesheet():
    """Brand 1.9.0 is vendored, glass and figures CSS included (#179)."""
    provenance = json.loads((SOURCE / "docs/brand-asset-provenance.json").read_text())
    native = json.loads((SOURCE / "docs/native-brand-assets.json").read_text())
    tokens = json.loads((SOURCE / "docs/pig-family-brand-tokens.json").read_text())
    assert tokens["version"] == provenance["version"] == native["version"] == "1.9.0"
    glass = next(a for a in provenance["assets"] if a["path"] == "tokens/pig-glass.css")
    assert glass["copiedTo"] == "client/src/brand/pig-glass.css"
    css = (SOURCE / glass["copiedTo"]).read_text()
    for rule in (".pig-glass", ".pig-amount", "prefers-reduced-transparency"):
        assert rule in css
    for generated in (
        "client/src/tokens.css",
        "swift/PigKit/Sources/PigBrand/Tokens.swift",
    ):
        assert "Pig family 1.9.0" in (SOURCE / generated).read_text().splitlines()[0]
    # The web loads the glass sheet and maps the --pig-* names it uses.
    assert (
        '@import "./brand/pig-glass.css";'
        in (SOURCE / "client/src/Base.css").read_text()
    )
    aliases = (SOURCE / "client/src/tokens.css").read_text()
    for name in (
        "accent",
        "on-accent",
        "text-accent",
        "surface",
        "border",
        "control-border",
    ):
        assert f"--pig-{name}: var(--{name});" in aliases
