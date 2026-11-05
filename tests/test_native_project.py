"""The native project generator and the committed Xcode project must agree on
the iPhone app's Info.plist contents, so regenerating never drops a key."""

import importlib.util
import plistlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate_native_project.py"


# The container image tests mount only tests/; the native sources are absent
# there and checked by the native workflow.
@pytest.mark.skipif(
    not GENERATOR.exists(), reason="native project sources are not in this image"
)
def test_privacy_descriptions_survive_regeneration():
    spec = importlib.util.spec_from_file_location("generate_native_project", GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    IPHONE_INFO = generator.IPHONE_INFO
    project = (ROOT / "swift/PigMusic.xcodeproj/project.pbxproj").read_text()
    info = plistlib.loads((ROOT / "swift/PigMusic/Info.plist").read_bytes())
    # Local network, Apple Music for its playlists in search (D-66), and the
    # microphone for "What's playing?" (D-70).
    keys = [
        "INFOPLIST_KEY_NSLocalNetworkUsageDescription",
        "INFOPLIST_KEY_NSAppleMusicUsageDescription",
        "INFOPLIST_KEY_NSMicrophoneUsageDescription",
    ]
    for key in keys:
        assert IPHONE_INFO[key]
        # Debug and Release of the iPhone target carry the generator's value.
        found = re.findall(re.escape(key) + r' = "([^"]*)";', project)
        assert found == [IPHONE_INFO[key]] * 2
        # One source only: the hand-written Info.plist does not repeat it.
        assert key.removeprefix("INFOPLIST_KEY_") not in info
    assert info["UIBackgroundModes"] == ["audio"]


@pytest.mark.skipif(
    not GENERATOR.exists(), reason="native project sources are not in this image"
)
def test_both_apps_are_ready_for_app_store_upload():
    """TestFlight: each app bundles Apple's privacy manifest and declares
    standard encryption only, in the project and its generator alike."""
    project = (ROOT / "swift/PigMusic.xcodeproj/project.pbxproj").read_text()
    generator = GENERATOR.read_text()
    for app in ("PigMusic", "PigMusicWatch"):
        manifest = ROOT / f"swift/{app}/PrivacyInfo.xcprivacy"
        info = plistlib.loads(manifest.read_bytes())
        assert info["NSPrivacyTracking"] is False
        reasons = {
            entry["NSPrivacyAccessedAPIType"]
            for entry in info["NSPrivacyAccessedAPITypes"]
        }
        assert reasons >= {
            "NSPrivacyAccessedAPICategoryUserDefaults",
            "NSPrivacyAccessedAPICategoryDiskSpace",
        }
        path = f"{app}/PrivacyInfo.xcprivacy"
        assert f"{path} in Resources */," in project and path in generator
    assert project.count("INFOPLIST_KEY_ITSAppUsesNonExemptEncryption = NO;") == 4
    assert generator.count('"INFOPLIST_KEY_ITSAppUsesNonExemptEncryption": "NO"') == 2


@pytest.mark.skipif(
    not GENERATOR.exists(), reason="native project sources are not in this image"
)
def test_the_share_extension_is_embedded_with_the_app_group():
    """Share → PigMusic (D-70): the extension target the generator makes is
    in the committed project under the generator's ids, embedded in the app,
    and both carry the App Group the shared songs wait in."""
    spec = importlib.util.spec_from_file_location("generate_native_project", GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    project = (ROOT / "swift/PigMusic.xcodeproj/project.pbxproj").read_text()
    source = GENERATOR.read_text()
    target = generator.identity("share-target")
    assert f"{target} /* PigMusicShare */ = {{" in project
    assert f"{target} /* PigMusicShare */," in project  # in the project's targets
    embed = generator.identity("embed-share")
    assert f"{embed} /* Embed Foundation Extensions */," in project  # an app phase
    assert "PigMusicShare.appex in Embed Foundation Extensions */," in project
    bundles = re.findall(r"PRODUCT_BUNDLE_IDENTIFIER = ([\w.]+);", project)
    assert bundles.count("au.com.pig.music.share") == 2
    assert '"PRODUCT_BUNDLE_IDENTIFIER": "au.com.pig.music.share"' in source
    for entitlements in (
        "PigMusic/PigMusic.entitlements",
        "PigMusicShare/PigMusicShare.entitlements",
    ):
        assert project.count(f"CODE_SIGN_ENTITLEMENTS = {entitlements};") == 2
        assert f'"CODE_SIGN_ENTITLEMENTS": "{entitlements}"' in source
        groups = plistlib.loads((ROOT / "swift" / entitlements).read_bytes())
        assert groups == {
            "com.apple.security.application-groups": ["group.au.com.pig.music"]
        }
    info = plistlib.loads((ROOT / "swift/PigMusicShare/Info.plist").read_bytes())
    extension = info["NSExtension"]
    assert extension["NSExtensionPointIdentifier"] == "com.apple.share-services"
    assert extension["NSExtensionPrincipalClass"].endswith(".ShareViewController")
    # The extension's version follows the app's, as App Store uploads require.
    versions = re.findall(r"CURRENT_PROJECT_VERSION = (\w+);", project)
    assert len(versions) == 6 and len(set(versions)) == 1
