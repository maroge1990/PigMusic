"""The simulator tap-through tests' own Xcode project (swift/PigMusicUITests).

Kept apart from PigMusic.xcodeproj so the app project and its TestFlight
builds never change for a test: each bundle drives the app already installed
in the simulator by its bundle identifier. `--check` fails when the committed
project differs from what this writes.

    python3 scripts/generate_ui_test_project.py [--check]
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "swift" / "PigMusicUITests"
XCODE_VERSION = "2710"

# (target, source folder, platform settings)
TARGETS = (
    (
        "PigMusicUITests",
        "iPhone",
        {
            "SDKROOT": "iphoneos",
            "SUPPORTED_PLATFORMS": "iphoneos iphonesimulator",
            "IPHONEOS_DEPLOYMENT_TARGET": "26.0",
            "TARGETED_DEVICE_FAMILY": "1,2",
        },
    ),
    (
        "PigMusicWatchUITests",
        "Watch",
        {
            "SDKROOT": "watchos",
            "SUPPORTED_PLATFORMS": "watchos watchsimulator",
            "WATCHOS_DEPLOYMENT_TARGET": "11.0",
            "TARGETED_DEVICE_FAMILY": "4",
        },
    ),
)


def identity(name):
    return hashlib.sha256(("ui-tests:" + name).encode()).hexdigest()[:24].upper()


def quote(value):
    if isinstance(value, list):
        return "(" + ",".join(quote(v) for v in value) + ")"
    if isinstance(value, dict):
        return "{" + "".join(f"{k} = {quote(v)};" for k, v in value.items()) + "}"
    return json.dumps(str(value))


def render():
    """{relative path: text} for the project file and one scheme per target."""
    objects = {}

    def add(object_name, isa, /, **fields):
        key = identity(object_name)
        objects[key] = {"isa": isa, **fields}
        return key

    group_children, products, targets, schemes = [], [], [], {}
    project = identity("project")
    for name, folder, platform in TARGETS:
        files = []
        built = []
        for path in sorted((TESTS / folder).glob("*.swift")):
            ref = add(
                f"{folder}/{path.name}",
                "PBXFileReference",
                lastKnownFileType="sourcecode.swift",
                path=path.name,
                sourceTree="<group>",
            )
            files.append(ref)
            built.append(
                add(f"build-{folder}/{path.name}", "PBXBuildFile", fileRef=ref)
            )
        group_children.append(
            add(
                f"group-{folder}",
                "PBXGroup",
                children=files,
                path=folder,
                sourceTree="<group>",
            )
        )
        product = add(
            f"product-{name}",
            "PBXFileReference",
            explicitFileType="wrapper.cfbundle",
            includeInIndex="0",
            path=f"{name}.xctest",
            sourceTree="BUILT_PRODUCTS_DIR",
        )
        products.append(product)
        configs = []
        for config in ("Debug", "Release"):
            configs.append(
                add(
                    f"{name}-{config}",
                    "XCBuildConfiguration",
                    buildSettings={
                        "PRODUCT_NAME": "$(TARGET_NAME)",
                        "PRODUCT_BUNDLE_IDENTIFIER": "au.com.pig.music." + name.lower(),
                        "GENERATE_INFOPLIST_FILE": "YES",
                        "SWIFT_VERSION": "6.0",
                        "SWIFT_STRICT_CONCURRENCY": "complete",
                        "CODE_SIGN_STYLE": "Automatic",
                        "ONLY_ACTIVE_ARCH": "YES",
                        "ENABLE_USER_SCRIPT_SANDBOXING": "YES",
                        **platform,
                    },
                    name=config,
                )
            )
        config_list = add(
            f"{name}-configs",
            "XCConfigurationList",
            buildConfigurations=configs,
            defaultConfigurationIsVisible="0",
            defaultConfigurationName="Debug",
        )
        target = add(
            f"target-{name}",
            "PBXNativeTarget",
            buildConfigurationList=config_list,
            buildPhases=[
                add(
                    f"sources-{name}",
                    "PBXSourcesBuildPhase",
                    buildActionMask="2147483647",
                    files=built,
                    runOnlyForDeploymentPostprocessing="0",
                ),
                add(
                    f"frameworks-{name}",
                    "PBXFrameworksBuildPhase",
                    buildActionMask="2147483647",
                    files=[],
                    runOnlyForDeploymentPostprocessing="0",
                ),
            ],
            buildRules=[],
            dependencies=[],
            name=name,
            productName=name,
            productReference=product,
            productType="com.apple.product-type.bundle.ui-testing",
        )
        targets.append(target)
        reference = (
            f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{target}" '
            f'BuildableName="{name}.xctest" BlueprintName="{name}" '
            f'ReferencedContainer="container:PigMusicUITests.xcodeproj"/>'
        )
        schemes[f"PigMusicUITests.xcodeproj/xcshareddata/xcschemes/{name}.xcscheme"] = (
            f"""<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="{XCODE_VERSION}" version="1.7">
<BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="NO" buildForProfiling="NO" buildForArchiving="NO" buildForAnalyzing="NO">{reference}</BuildActionEntry></BuildActionEntries></BuildAction>
<TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.DebuggerFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"><Testables><TestableReference skipped="NO">{reference}</TestableReference></Testables></TestAction>
<LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.DebuggerFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" allowLocationSimulation="YES"/>
</Scheme>
"""
        )
    products_group = add(
        "products", "PBXGroup", children=products, name="Products", sourceTree="<group>"
    )
    main = add(
        "main",
        "PBXGroup",
        children=[*group_children, products_group],
        sourceTree="<group>",
    )
    project_configs = [
        add(
            f"project-{config}",
            "XCBuildConfiguration",
            buildSettings={"SWIFT_VERSION": "6.0", "ALWAYS_SEARCH_USER_PATHS": "NO"},
            name=config,
        )
        for config in ("Debug", "Release")
    ]
    objects[project] = {
        "isa": "PBXProject",
        "attributes": {
            "BuildIndependentTargetsInParallel": "1",
            "LastUpgradeCheck": XCODE_VERSION,
        },
        "buildConfigurationList": add(
            "project-configs",
            "XCConfigurationList",
            buildConfigurations=project_configs,
            defaultConfigurationIsVisible="0",
            defaultConfigurationName="Debug",
        ),
        "compatibilityVersion": "Xcode 14.0",
        "developmentRegion": "en",
        "knownRegions": ["en", "Base"],
        "mainGroup": main,
        "productRefGroup": products_group,
        "projectDirPath": "",
        "projectRoot": "",
        "targets": targets,
    }
    body = "\n".join(f"{key} = {quote(value)};" for key, value in objects.items())
    return {
        "PigMusicUITests.xcodeproj/project.pbxproj": "// !$*UTF8*$!\n"
        f"{{archiveVersion = 1; classes = {{}}; objectVersion = 56; objects = {{\n{body}\n}}; rootObject = {project};}}\n",
        **schemes,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale = []
    for relative, text in render().items():
        path = TESTS / relative
        if args.check:
            if not path.is_file() or path.read_text() != text:
                stale.append(relative)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if stale:
        print(
            "Out of date (run scripts/generate_ui_test_project.py):", *stale, sep="\n  "
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
