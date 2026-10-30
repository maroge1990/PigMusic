"""Reproducible production iPhone/iPad and Watch Xcode project; no signing identities."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = ROOT / "swift"


# Xcode's recommended project settings (Xcode 27.1, Base_ProjectSettings
# template). Kept here so the "Update to recommended settings" prompt stops.
# The project's own Swift settings below take precedence.
XCODE_VERSION = "2710"

# Privacy descriptions live in the iPhone target's build settings (Xcode's
# generated Info.plist), not in PigMusic/Info.plist; tests check both agree.
IPHONE_INFO = {
    "INFOPLIST_KEY_NSLocalNetworkUsageDescription": "PigMusic connects to your private music server to browse and play your library.",
}
RECOMMENDED = {
    "ALWAYS_SEARCH_USER_PATHS": "NO",
    "ASSETCATALOG_COMPILER_GENERATE_SWIFT_ASSET_SYMBOL_EXTENSIONS": "YES",
    "CLANG_ANALYZER_NONNULL": "YES",
    "CLANG_ANALYZER_NUMBER_OBJECT_CONVERSION": "YES_AGGRESSIVE",
    "CLANG_CXX_LANGUAGE_STANDARD": "gnu++20",
    "CLANG_ENABLE_MODULES": "YES",
    "CLANG_ENABLE_OBJC_ARC": "YES",
    "CLANG_ENABLE_OBJC_WEAK": "YES",
    "CLANG_WARN_BLOCK_CAPTURE_AUTORELEASING": "YES",
    "CLANG_WARN_BOOL_CONVERSION": "YES",
    "CLANG_WARN_COMMA": "YES",
    "CLANG_WARN_CONSTANT_CONVERSION": "YES",
    "CLANG_WARN_DEPRECATED_OBJC_IMPLEMENTATIONS": "YES",
    "CLANG_WARN_DIRECT_OBJC_ISA_USAGE": "YES_ERROR",
    "CLANG_WARN_DOCUMENTATION_COMMENTS": "YES",
    "CLANG_WARN_EMPTY_BODY": "YES",
    "CLANG_WARN_ENUM_CONVERSION": "YES",
    "CLANG_WARN_INFINITE_RECURSION": "YES",
    "CLANG_WARN_INT_CONVERSION": "YES",
    "CLANG_WARN_NON_LITERAL_NULL_CONVERSION": "YES",
    "CLANG_WARN_OBJC_IMPLICIT_RETAIN_SELF": "YES",
    "CLANG_WARN_OBJC_LITERAL_CONVERSION": "YES",
    "CLANG_WARN_OBJC_ROOT_CLASS": "YES_ERROR",
    "CLANG_WARN_QUOTED_INCLUDE_IN_FRAMEWORK_HEADER": "YES",
    "CLANG_WARN_RANGE_LOOP_ANALYSIS": "YES",
    "CLANG_WARN_STRICT_PROTOTYPES": "YES",
    "CLANG_WARN_SUSPICIOUS_MOVE": "YES",
    "CLANG_WARN_UNGUARDED_AVAILABILITY": "YES_AGGRESSIVE",
    "CLANG_WARN_UNREACHABLE_CODE": "YES",
    "CLANG_WARN__DUPLICATE_METHOD_MATCH": "YES",
    "COPY_PHASE_STRIP": "NO",
    "ENABLE_STRICT_OBJC_MSGSEND": "YES",
    "ENABLE_USER_SCRIPT_SANDBOXING": "YES",
    "GCC_C_LANGUAGE_STANDARD": "gnu17",
    "GCC_NO_COMMON_BLOCKS": "YES",
    "GCC_WARN_64_TO_32_BIT_CONVERSION": "YES",
    "GCC_WARN_ABOUT_RETURN_TYPE": "YES_ERROR",
    "GCC_WARN_UNDECLARED_SELECTOR": "YES",
    "GCC_WARN_UNINITIALIZED_AUTOS": "YES_AGGRESSIVE",
    "GCC_WARN_UNUSED_FUNCTION": "YES",
    "GCC_WARN_UNUSED_VARIABLE": "YES",
    "LOCALIZATION_PREFERS_STRING_CATALOGS": "YES",
}
RECOMMENDED_DEBUG = {
    "DEBUG_INFORMATION_FORMAT": "dwarf",
    "ENABLE_TESTABILITY": "YES",
    "GCC_DYNAMIC_NO_PIC": "NO",
    "GCC_OPTIMIZATION_LEVEL": "0",
    "GCC_PREPROCESSOR_DEFINITIONS": "DEBUG=1 $(inherited)",
    "ONLY_ACTIVE_ARCH": "YES",
}
RECOMMENDED_RELEASE = {
    "DEBUG_INFORMATION_FORMAT": "dwarf-with-dsym",
    "ENABLE_NS_ASSERTIONS": "NO",
}


def identity(name):
    return hashlib.sha256(name.encode()).hexdigest()[:24].upper()


def generate():
    objects = {}

    def add(object_name, isa, **fields):
        key = identity(object_name)
        objects[key] = {"isa": isa, **fields}
        return key

    def quote(value):
        if isinstance(value, list):
            return "(" + ",".join(quote(v) for v in value) + ")"
        if isinstance(value, dict):
            return "{" + "".join(f"{k} = {quote(v)};" for k, v in value.items()) + "}"
        return json.dumps(str(value))

    files = []
    sources = []
    for path in sorted((SWIFT / "PigMusic").glob("*.swift")):
        ref = add(
            str(path.name),
            "PBXFileReference",
            lastKnownFileType="sourcecode.swift",
            path=f"PigMusic/{path.name}",
            sourceTree="<group>",
        )
        files.append(ref)
        sources.append(add(f"build-{path.name}", "PBXBuildFile", fileRef=ref))
    shared = add(
        "companion-link",
        "PBXFileReference",
        lastKnownFileType="sourcecode.swift",
        path="Shared/CompanionLink.swift",
        sourceTree="<group>",
    )
    files.append(shared)
    sources.append(add("phone-companion-link", "PBXBuildFile", fileRef=shared))
    watch_sources = [add("watch-companion-link", "PBXBuildFile", fileRef=shared)]
    for path in sorted((SWIFT / "PigMusicWatch").glob("*.swift")):
        ref = add(
            "watch-" + path.name,
            "PBXFileReference",
            lastKnownFileType="sourcecode.swift",
            path=f"PigMusicWatch/{path.name}",
            sourceTree="<group>",
        )
        files.append(ref)
        watch_sources.append(
            add("watch-build-" + path.name, "PBXBuildFile", fileRef=ref)
        )
    watch_product = add(
        "watch-product",
        "PBXFileReference",
        explicitFileType="wrapper.application",
        path="PigMusicWatch.app",
        sourceTree="BUILT_PRODUCTS_DIR",
    )
    assets = add(
        "assets",
        "PBXFileReference",
        lastKnownFileType="folder.assetcatalog",
        path="PigMusic/Assets.xcassets",
        sourceTree="<group>",
    )
    files.append(assets)
    asset_build = add("asset-build", "PBXBuildFile", fileRef=assets)
    icon = add(
        "phone-icon",
        "PBXFileReference",
        lastKnownFileType="folder.icon",
        path="PigMusic/AppIcon.icon",
        sourceTree="<group>",
    )
    launch = add(
        "launch-screen",
        "PBXFileReference",
        lastKnownFileType="file.storyboard",
        path="PigMusic/LaunchScreen.storyboard",
        sourceTree="<group>",
    )
    # Apple's privacy manifest: why the app reads settings, file dates, free
    # space and time since start-up (required for App Store uploads).
    privacy = add(
        "phone-privacy",
        "PBXFileReference",
        lastKnownFileType="text.xml",
        path="PigMusic/PrivacyInfo.xcprivacy",
        sourceTree="<group>",
    )
    files.extend([icon, launch, privacy])
    icon_build = add("phone-icon-build", "PBXBuildFile", fileRef=icon)
    launch_build = add("launch-screen-build", "PBXBuildFile", fileRef=launch)
    privacy_build = add("phone-privacy-build", "PBXBuildFile", fileRef=privacy)
    tone = add(
        "demo-tone",
        "PBXFileReference",
        lastKnownFileType="file",
        path="PigMusic/demo-tone.m4a",
        sourceTree="<group>",
    )
    files.append(tone)
    tone_build = add("demo-tone-build", "PBXBuildFile", fileRef=tone)
    product = add(
        "product",
        "PBXFileReference",
        explicitFileType="wrapper.application",
        path="PigMusic.app",
        sourceTree="BUILT_PRODUCTS_DIR",
    )
    products = add(
        "products",
        "PBXGroup",
        children=[product, watch_product],
        name="Products",
        sourceTree="<group>",
    )
    main = add("main", "PBXGroup", children=files + [products], sourceTree="<group>")
    package = add("package", "XCLocalSwiftPackageReference", relativePath="PigKit")
    dependencies = []
    frameworks = []
    for name in (
        "PigAPI",
        "PigBrand",
        "PigDomain",
        "PigStore",
        "PigSync",
        "PigPlayback",
        "PigDownloads",
        "PigCompanion",
    ):
        dependency = add(
            name, "XCSwiftPackageProductDependency", package=package, productName=name
        )
        dependencies.append(dependency)
        frameworks.append(
            add(f"framework-{name}", "PBXBuildFile", productRef=dependency)
        )
    phases = [
        add(
            "sources",
            "PBXSourcesBuildPhase",
            buildActionMask="2147483647",
            files=sources,
            runOnlyForDeploymentPostprocessing="0",
        ),
        add(
            "resources",
            "PBXResourcesBuildPhase",
            buildActionMask="2147483647",
            files=[asset_build, tone_build, icon_build, launch_build, privacy_build],
            runOnlyForDeploymentPostprocessing="0",
        ),
        add(
            "frameworks",
            "PBXFrameworksBuildPhase",
            buildActionMask="2147483647",
            files=frameworks,
            runOnlyForDeploymentPostprocessing="0",
        ),
    ]
    watch_dependencies = []
    watch_frameworks = []
    for name in ("PigBrand", "PigCompanion", "PigDomain", "PigPlayback"):
        dependency = add(
            "watch-" + name,
            "XCSwiftPackageProductDependency",
            package=package,
            productName=name,
        )
        watch_dependencies.append(dependency)
        watch_frameworks.append(
            add("watch-framework-" + name, "PBXBuildFile", productRef=dependency)
        )
    watch_assets = add(
        "watch-assets",
        "PBXFileReference",
        lastKnownFileType="folder.assetcatalog",
        path="PigMusicWatch/Assets.xcassets",
        sourceTree="<group>",
    )
    watch_privacy = add(
        "watch-privacy",
        "PBXFileReference",
        lastKnownFileType="text.xml",
        path="PigMusicWatch/PrivacyInfo.xcprivacy",
        sourceTree="<group>",
    )
    objects[main]["children"].insert(0, watch_assets)
    objects[main]["children"].insert(1, watch_privacy)
    watch_phases = [
        add(
            "watch-resources",
            "PBXResourcesBuildPhase",
            buildActionMask="2147483647",
            files=[
                add("watch-assets-build", "PBXBuildFile", fileRef=watch_assets),
                add("watch-privacy-build", "PBXBuildFile", fileRef=watch_privacy),
            ],
            runOnlyForDeploymentPostprocessing="0",
        ),
        add(
            "watch-sources",
            "PBXSourcesBuildPhase",
            buildActionMask="2147483647",
            files=watch_sources,
            runOnlyForDeploymentPostprocessing="0",
        ),
        add(
            "watch-frameworks",
            "PBXFrameworksBuildPhase",
            buildActionMask="2147483647",
            files=watch_frameworks,
            runOnlyForDeploymentPostprocessing="0",
        ),
    ]
    phases.append(
        add(
            "embed-watch",
            "PBXCopyFilesBuildPhase",
            buildActionMask="2147483647",
            dstPath="$(CONTENTS_FOLDER_PATH)/Watch",
            dstSubfolderSpec="16",
            files=[
                add(
                    "embed-watch-file",
                    "PBXBuildFile",
                    fileRef=watch_product,
                    settings={"ATTRIBUTES": ["RemoveHeadersOnCopy"]},
                )
            ],
            name="Embed Watch Content",
            runOnlyForDeploymentPostprocessing="0",
        )
    )
    watch_configs = []
    project_configs = []
    target_configs = []
    for name in ("Debug", "Release"):
        common = {
            **RECOMMENDED,
            **(RECOMMENDED_DEBUG if name == "Debug" else RECOMMENDED_RELEASE),
            "CLANG_ENABLE_MODULES": "YES",
            "SWIFT_VERSION": "6.0",
            "SWIFT_STRICT_CONCURRENCY": "complete",
            "SWIFT_OPTIMIZATION_LEVEL": "-Onone" if name == "Debug" else "-O",
        }
        if name == "Debug":
            common["SWIFT_ACTIVE_COMPILATION_CONDITIONS"] = "DEBUG $(inherited)"
        project_configs.append(
            add(
                f"project-{name}",
                "XCBuildConfiguration",
                buildSettings=common,
                name=name,
            )
        )
        settings = {
            "PRODUCT_NAME": "$(TARGET_NAME)",
            "PRODUCT_BUNDLE_IDENTIFIER": "au.com.pig.music",
            "GENERATE_INFOPLIST_FILE": "YES",
            "INFOPLIST_FILE": "PigMusic/Info.plist",
            "MARKETING_VERSION": "0.2.0",
            "CURRENT_PROJECT_VERSION": "15",
            "SDKROOT": "iphoneos",
            "SUPPORTED_PLATFORMS": "iphoneos iphonesimulator",
            "IPHONEOS_DEPLOYMENT_TARGET": "26.0",
            "TARGETED_DEVICE_FAMILY": "1,2",
            "CODE_SIGN_STYLE": "Automatic",
            "ASSETCATALOG_COMPILER_APPICON_NAME": "AppIcon",
            "INFOPLIST_KEY_CFBundleDisplayName": "PigMusic",
            # HTTPS only: no export-compliance question on each upload.
            "INFOPLIST_KEY_ITSAppUsesNonExemptEncryption": "NO",
            "INFOPLIST_KEY_UILaunchStoryboardName": "LaunchScreen",
            "INFOPLIST_KEY_UIApplicationSceneManifest_Generation": "YES",
            "INFOPLIST_KEY_UISupportedInterfaceOrientations": "UIInterfaceOrientationPortrait UIInterfaceOrientationLandscapeLeft UIInterfaceOrientationLandscapeRight",
            "INFOPLIST_KEY_UISupportedInterfaceOrientations_iPad": "UIInterfaceOrientationPortrait UIInterfaceOrientationPortraitUpsideDown UIInterfaceOrientationLandscapeLeft UIInterfaceOrientationLandscapeRight",
            **IPHONE_INFO,
        }
        watch_settings = {
            "PRODUCT_NAME": "$(TARGET_NAME)",
            "PRODUCT_BUNDLE_IDENTIFIER": "au.com.pig.music.watchkitapp",
            "GENERATE_INFOPLIST_FILE": "YES",
            "INFOPLIST_FILE": "PigMusicWatch/Info.plist",
            "MARKETING_VERSION": "0.2.0",
            "CURRENT_PROJECT_VERSION": "15",
            "SDKROOT": "watchos",
            "SUPPORTED_PLATFORMS": "watchos watchsimulator",
            "WATCHOS_DEPLOYMENT_TARGET": "11.0",
            "TARGETED_DEVICE_FAMILY": "4",
            "CODE_SIGN_STYLE": "Automatic",
            "SKIP_INSTALL": "YES",
            "ASSETCATALOG_COMPILER_APPICON_NAME": "AppIcon",
            "INFOPLIST_KEY_CFBundleDisplayName": "PigMusic",
            # HTTPS only: no export-compliance question on each upload.
            "INFOPLIST_KEY_ITSAppUsesNonExemptEncryption": "NO",
        }
        watch_configs.append(
            add(
                f"watch-target-{name}",
                "XCBuildConfiguration",
                buildSettings=watch_settings,
                name=name,
            )
        )
        target_configs.append(
            add(
                f"target-{name}",
                "XCBuildConfiguration",
                buildSettings=settings,
                name=name,
            )
        )
    project_list = add(
        "project-list",
        "XCConfigurationList",
        buildConfigurations=project_configs,
        defaultConfigurationIsVisible="0",
        defaultConfigurationName="Release",
    )
    target_list = add(
        "target-list",
        "XCConfigurationList",
        buildConfigurations=target_configs,
        defaultConfigurationIsVisible="0",
        defaultConfigurationName="Release",
    )
    watch_list = add(
        "watch-list",
        "XCConfigurationList",
        buildConfigurations=watch_configs,
        defaultConfigurationIsVisible="0",
        defaultConfigurationName="Release",
    )
    watch_target = add(
        "watch-target",
        "PBXNativeTarget",
        buildConfigurationList=watch_list,
        buildPhases=watch_phases,
        buildRules=[],
        dependencies=[],
        name="PigMusicWatch",
        productName="PigMusicWatch",
        productReference=watch_product,
        productType="com.apple.product-type.application",
        packageProductDependencies=watch_dependencies,
    )
    proxy = add(
        "watch-proxy",
        "PBXContainerItemProxy",
        containerPortal=identity("project"),
        proxyType="1",
        remoteGlobalIDString=watch_target,
        remoteInfo="PigMusicWatch",
    )
    watch_dependency = add(
        "watch-target-dependency",
        "PBXTargetDependency",
        target=watch_target,
        targetProxy=proxy,
    )
    target = add(
        "target",
        "PBXNativeTarget",
        buildConfigurationList=target_list,
        buildPhases=phases,
        buildRules=[],
        dependencies=[watch_dependency],
        name="PigMusic",
        productName="PigMusic",
        productReference=product,
        productType="com.apple.product-type.application",
        packageProductDependencies=dependencies,
    )
    project = add(
        "project",
        "PBXProject",
        attributes={
            "BuildIndependentTargetsInParallel": "1",
            "LastUpgradeCheck": XCODE_VERSION,
        },
        buildConfigurationList=project_list,
        compatibilityVersion="Xcode 14.0",
        developmentRegion="en",
        knownRegions=["en", "Base"],
        mainGroup=main,
        productRefGroup=products,
        projectDirPath="",
        projectRoot="",
        targets=[target, watch_target],
        packageReferences=[package],
    )
    directory = SWIFT / "PigMusic.xcodeproj"
    directory.mkdir(exist_ok=True)
    body = "\n".join(f"{key} = {quote(value)};" for key, value in objects.items())
    (directory / "project.pbxproj").write_text(
        f"// !$*UTF8*$!\n{{archiveVersion = 1; classes = {{}}; objectVersion = 56; objects = {{\n{body}\n}}; rootObject = {project};}}\n"
    )
    schemes = directory / "xcshareddata" / "xcschemes"
    schemes.mkdir(parents=True, exist_ok=True)
    reference = f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{target}" BuildableName="PigMusic.app" BlueprintName="PigMusic" ReferencedContainer="container:PigMusic.xcodeproj"/>'
    (schemes / "PigMusic.xcscheme").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="{XCODE_VERSION}" version="1.3">
<BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">{reference}</BuildActionEntry></BuildActionEntries></BuildAction>
<LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0">{reference}</BuildableProductRunnable></LaunchAction>
<ProfileAction buildConfiguration="Release"><BuildableProductRunnable runnableDebuggingMode="0">{reference}</BuildableProductRunnable></ProfileAction>
<AnalyzeAction buildConfiguration="Debug"/><ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>
"""
    )

    watch_reference = f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{watch_target}" BuildableName="PigMusicWatch.app" BlueprintName="PigMusicWatch" ReferencedContainer="container:PigMusic.xcodeproj"/>'
    (schemes / "PigMusicWatch.xcscheme").write_text(
        (schemes / "PigMusic.xcscheme").read_text().replace(reference, watch_reference)
    )


if __name__ == "__main__":
    generate()
