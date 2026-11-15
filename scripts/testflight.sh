#!/bin/sh
# Archive PigMusic (Release, with its Watch app and share extension) and upload it to TestFlight. Run ON THE MAC,
# from its git clone (~/pigmusic-release), over plain SSH. The archive is unsigned; the export signs it with Apple's
# cloud-managed distribution certificate through an App Store Connect API key, so the keychain is never needed.
#
#   sh scripts/testflight.sh            test, archive, check the signed app, upload
#   sh scripts/testflight.sh --archive  test, archive and check the signed app only (no upload)
#
# The key must have the Admin role (cloud signing needs it). Settings come from the environment or
# ~/.pigmusic-testflight.env; the names are in .env.example. Refuses on uncommitted changes, uploads only after the
# PigKit tests pass on this commit, and only when the signed app and share extension keep their App Group. The build
# number is the commit count, so every upload from a later commit has a new one.
set -eu
cd "$(dirname "$0")/.."
git rev-parse --git-dir >/dev/null 2>&1 || { echo "testflight: run from the Mac's git clone, not a copy" >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "testflight: commit your changes first" >&2; exit 1; }
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
[ -f "$HOME/.pigmusic-testflight.env" ] && . "$HOME/.pigmusic-testflight.env"
: "${PIGMUSIC_TEAM_ID:?set PIGMUSIC_TEAM_ID}" "${PIGMUSIC_ASC_KEY_ID:?set PIGMUSIC_ASC_KEY_ID}"
: "${PIGMUSIC_ASC_ISSUER_ID:?set PIGMUSIC_ASC_ISSUER_ID}" "${PIGMUSIC_ASC_KEY_PATH:?set PIGMUSIC_ASC_KEY_PATH}"
MODE="${1:-}"
# The Mac is shared and short of space: stop before a build would leave it under about 13 GB.
FREE_GB="$(df -g / | awk 'NR==2 {print $4}')"
[ "$FREE_GB" -ge 15 ] || { echo "testflight: only ${FREE_GB} GB free on the Mac; need 15" >&2; exit 1; }
BUILD="$(git rev-list --count HEAD)"
AUTH="-allowProvisioningUpdates -authenticationKeyPath $PIGMUSIC_ASC_KEY_PATH -authenticationKeyID $PIGMUSIC_ASC_KEY_ID -authenticationKeyIssuerID $PIGMUSIC_ASC_ISSUER_ID"
OUT="build/testflight"
ARCHIVE="$OUT/PigMusic.xcarchive"
rm -rf "$OUT"
mkdir -p "$OUT"
options() { # $1 = destination (export or upload), $2 = file
  cp scripts/ExportOptions-AppStore.plist "$2"
  /usr/libexec/PlistBuddy -c "Add :teamID string $PIGMUSIC_TEAM_ID" -c "Set :destination $1" "$2"
}
options export "$OUT/ExportCheck.plist"
options upload "$OUT/ExportUpload.plist"

echo "== tests (PigKit)"
swift test -q --package-path swift/PigKit

echo "== archive build $BUILD ($(git rev-parse --short HEAD))"
xcodebuild -quiet -project swift/PigMusic.xcodeproj -scheme PigMusic -configuration Release \
  -destination "generic/platform=iOS" -archivePath "$ARCHIVE" -skipPackagePluginValidation \
  DEVELOPMENT_TEAM="$PIGMUSIC_TEAM_ID" CURRENT_PROJECT_VERSION="$BUILD" CODE_SIGNING_ALLOWED=NO archive

# An unsigned archive carries no entitlements, and the export signs with only what the archived binaries ask for:
# stamp each bundle with a local (ad hoc) signature holding its entitlements, innermost first, so the App Group
# survives. The export replaces these signatures with the cloud distribution one.
APPS="$ARCHIVE/Products/Applications/PigMusic.app"
codesign -f -s - "$APPS/Watch/PigMusicWatch.app"
codesign -f -s - --entitlements swift/PigMusicShare/PigMusicShare.entitlements "$APPS/PlugIns/PigMusicShare.appex"
codesign -f -s - --entitlements swift/PigMusic/PigMusic.entitlements "$APPS"

echo "== sign a local copy and check it"
# shellcheck disable=SC2086
xcodebuild -exportArchive -archivePath "$ARCHIVE" -exportOptionsPlist "$OUT/ExportCheck.plist" \
  -exportPath "$OUT/check" $AUTH
(cd "$OUT/check" && unzip -q PigMusic.ipa)
APP="$OUT/check/Payload/PigMusic.app"
for bundle in "$APP" "$APP/PlugIns/PigMusicShare.appex"; do
  codesign -d --entitlements - --xml "$bundle" 2>/dev/null | grep -q "group.au.com.pig.music" \
    || { echo "testflight: $bundle lost its App Group when signed; not uploading" >&2; exit 1; }
done
[ -d "$APP/Watch/PigMusicWatch.app" ] || { echo "testflight: the Watch app is missing; not uploading" >&2; exit 1; }
for bundle in "$APP" "$APP/PlugIns/PigMusicShare.appex" "$APP/Watch/PigMusicWatch.app"; do
  version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$bundle/Info.plist")"
  [ "$version" = "$BUILD" ] || { echo "testflight: $bundle has build $version, not $BUILD" >&2; exit 1; }
done
echo "   app, share extension and Watch app signed, App Group kept, build $BUILD"
if [ "$MODE" = "--archive" ]; then
  echo "== archived and checked, not uploaded ($OUT)"
  exit 0
fi

echo "== upload build $BUILD"
# shellcheck disable=SC2086
xcodebuild -exportArchive -archivePath "$ARCHIVE" -exportOptionsPlist "$OUT/ExportUpload.plist" \
  -exportPath "$OUT/export" $AUTH
# The archive and its intermediates are large; the upload no longer needs them.
rm -rf "$OUT"
for dd in "$HOME"/Library/Developer/Xcode/DerivedData/PigMusic-*; do
  [ -f "$dd/info.plist" ] || continue
  case "$(/usr/libexec/PlistBuddy -c 'Print :WorkspacePath' "$dd/info.plist" 2>/dev/null)" in
    "$PWD"/*) rm -rf "$dd/Build/Intermediates.noindex/ArchiveIntermediates" ;;
  esac
done
echo "== done: build $BUILD uploaded; App Store Connect processes it in 10-30 minutes, then it appears under TestFlight"
