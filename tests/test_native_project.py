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
def test_local_network_description_survives_regeneration():
    spec = importlib.util.spec_from_file_location("generate_native_project", GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    IPHONE_INFO = generator.IPHONE_INFO
    project = (ROOT / "swift/PigMusic.xcodeproj/project.pbxproj").read_text()
    info = plistlib.loads((ROOT / "swift/PigMusic/Info.plist").read_bytes())
    key = "INFOPLIST_KEY_NSLocalNetworkUsageDescription"
    assert IPHONE_INFO[key]
    # Debug and Release of the iPhone target carry the generator's value.
    found = re.findall(re.escape(key) + r' = "([^"]*)";', project)
    assert found == [IPHONE_INFO[key]] * 2
    # One source only: the hand-written Info.plist does not repeat it.
    assert "NSLocalNetworkUsageDescription" not in info
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
