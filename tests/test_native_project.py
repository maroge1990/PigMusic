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
    # Audio plays in the background; a background refresh carries on with
    # Watch copies the server was still preparing (#219).
    assert info["UIBackgroundModes"] == ["audio", "fetch"]
    assert info["BGTaskSchedulerPermittedIdentifiers"] == [
        "au.com.pig.music.watchprepare"
    ]
    phone = (ROOT / "swift/PigMusic/WatchPhoneController.swift").read_text()
    assert 'static let refreshIdentifier = "au.com.pig.music.watchprepare"' in phone
    suspend = phone.split("  func suspend() async {", 1)[1].split("\n  }\n", 1)[0]
    assert suspend.rstrip().endswith("await handOff()")
    app = (ROOT / "swift/PigMusic/PigMusicApp.swift").read_text()
    assert ".backgroundTask(.appRefresh(WatchPhoneController.refreshIdentifier))" in app


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
    assert len(versions) == 8 and len(set(versions)) == 1


@pytest.mark.skipif(
    not GENERATOR.exists(), reason="native project sources are not in this image"
)
def test_the_watch_complication_is_embedded_in_the_watch_app_with_the_app_group():
    """D-125: the WidgetKit extension the generator makes is in the committed
    project under the generator's ids, built and embedded by the Watch app
    (not the iPhone app), and it and the Watch app share the App Group the
    status is written to."""
    spec = importlib.util.spec_from_file_location("generate_native_project", GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    project = (ROOT / "swift/PigMusic.xcodeproj/project.pbxproj").read_text()
    source = GENERATOR.read_text()
    target = generator.identity("complication-target")
    assert f"{target} /* PigMusicWatchComplication */ = {{" in project
    assert f"{target} /* PigMusicWatchComplication */," in project  # project targets
    # The Watch app embeds it into PlugIns (13), and builds it first.
    watch = project.split("/* PigMusicWatch */ = {\n", 1)[1].split("\t\t};\n", 1)[0]
    embed = generator.identity("embed-complication")
    assert f"{embed} /* Embed Foundation Extensions */," in watch
    assert generator.identity("complication-target-dependency") in watch
    phase = project.split(f"{embed} /* Embed Foundation Extensions */ = {{", 1)[1]
    phase = phase.split("};", 1)[0]
    assert "dstSubfolderSpec = 13;" in phase
    assert "PigMusicWatchComplication.appex in Embed Foundation Extensions */," in phase
    # The iPhone app embeds only the Watch app and the share extension.
    iphone = project.split("/* PigMusic */ = {\n\t\t\tisa = PBXNativeTarget;", 1)[1]
    iphone = iphone.split("\t\t};\n", 1)[0]
    assert embed not in iphone
    # A watchOS extension under the Watch app's bundle id, at WidgetKit's point.
    bundles = re.findall(r"PRODUCT_BUNDLE_IDENTIFIER = ([\w.]+);", project)
    assert bundles.count("au.com.pig.music.watchkitapp.widget") == 2
    assert '"au.com.pig.music.watchkitapp.widget"' in source
    for config in project.split(
        "PRODUCT_BUNDLE_IDENTIFIER = au.com.pig.music.watchkitapp.widget;"
    )[1:]:
        settings = config.split("};", 1)[0]
        assert "SDKROOT = watchos;" in settings
        assert "WATCHOS_DEPLOYMENT_TARGET = 11.0;" in settings
    info = plistlib.loads(
        (ROOT / "swift/PigMusicWatchComplication/Info.plist").read_bytes()
    )
    assert info == {
        "NSExtension": {"NSExtensionPointIdentifier": "com.apple.widgetkit-extension"}
    }
    for entitlements in (
        "PigMusicWatch/PigMusicWatch.entitlements",
        "PigMusicWatchComplication/PigMusicWatchComplication.entitlements",
    ):
        assert project.count(f"CODE_SIGN_ENTITLEMENTS = {entitlements};") == 2
        assert f'"CODE_SIGN_ENTITLEMENTS": "{entitlements}"' in source
        groups = plistlib.loads((ROOT / "swift" / entitlements).read_bytes())
        assert groups == {
            "com.apple.security.application-groups": ["group.au.com.pig.music"]
        }
    # The status is written to and read from that group.
    rules = ROOT / "swift/PigKit/Sources/PigCompanion/WatchComplication.swift"
    assert 'static let appGroup = "group.au.com.pig.music"' in rules.read_text()
    # Signed by the same team as the other targets.
    teams = re.findall(r"DEVELOPMENT_TEAM = (\w+);", project)
    assert len(teams) == 8 and len(set(teams)) == 1


# Xcode's old-style plist, enough for project.pbxproj: comments, quoted and
# bare strings, dictionaries and lists.
_TOKEN = re.compile(
    r'\s*(?:(/\*.*?\*/|//[^\n]*)|"((?:[^"\\]|\\.)*)"|([{}();=,])|([^\s{}();=,"]+))',
    re.S,
)


def _parse_pbxproj(text):
    tokens = []
    for comment, quoted, punct, bare in _TOKEN.findall(text):
        if punct:
            tokens.append(("p", punct))
        elif bare or quoted or not comment:
            tokens.append(("s", bare or quoted.encode().decode("unicode_escape")))
    position = 0

    def value():
        nonlocal position
        kind, token = tokens[position]
        position += 1
        if (kind, token) == ("p", "{"):
            result = {}
            while tokens[position] != ("p", "}"):
                key = tokens[position][1]
                assert tokens[position + 1] == ("p", "=")
                position += 2
                result[key] = value()
                assert tokens[position] == ("p", ";")
                position += 1
            position += 1
            return result
        if (kind, token) == ("p", "("):
            items = []
            while tokens[position] != ("p", ")"):
                items.append(value())
                if tokens[position] == ("p", ","):
                    position += 1
            position += 1
            return items
        return token

    return value()


def _objects(text):
    """The project's objects, without what Xcode adds when it saves."""
    objects = _parse_pbxproj(text)["objects"]
    for item in objects.values():
        item.get("buildSettings", {}).pop("DEVELOPMENT_TEAM", None)
        if item["isa"] == "PBXProject":
            item.pop("hasScannedForEncodings", None)
    return objects


@pytest.mark.skipif(
    not GENERATOR.exists(), reason="native project sources are not in this image"
)
def test_the_committed_project_is_the_generators(tmp_path):
    """The repo keeps Xcode's re-saved project, so new targets are patched in
    by hand under the generator's ids (the share extension, the Watch
    complication): object for object it must be what the generator makes,
    apart from the signing team Xcode adds."""
    spec = importlib.util.spec_from_file_location("generate_native_project", GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    for folder in (ROOT / "swift").iterdir():
        if folder.is_dir() and folder.name != "PigMusic.xcodeproj":
            (tmp_path / folder.name).symlink_to(folder)
    generator.SWIFT = tmp_path
    generator.generate()
    made = _objects((tmp_path / "PigMusic.xcodeproj/project.pbxproj").read_text())
    committed = _objects(
        (ROOT / "swift/PigMusic.xcodeproj/project.pbxproj").read_text()
    )
    assert committed.keys() == made.keys()
    for key, item in made.items():
        assert committed[key] == item, key
