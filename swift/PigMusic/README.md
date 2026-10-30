# PigMusic iPhone, iPad and Watch apps

Open `swift/PigMusic.xcodeproj` from the repository root. Scheme **PigMusic** builds the iPhone/iPad app and embeds **PigMusicWatch**. Build, test and signing commands are in [AGENTS.md](../../AGENTS.md); free-account installation is in [operations](../../docs/OPERATIONS.md#native-app-installation-free-apple-account); device acceptance is in [testing](../../docs/TESTING.md#native-device-acceptance).

The shared code lives in `swift/PigKit` (API client, store, sync, playback, downloads, companion link, brand); it is testable with `swift test --package-path swift/PigKit`. Administration stays in the web app. In Debug, `--demo` (and `--seamless-demo`) loads a synthetic offline library with a bundled tone; Release builds exclude them.

`PigAudioProbe` is an offline transition tool:

```sh
swift run --package-path swift/PigKit PigAudioProbe [--bounded] first second output.wav overlap-seconds
```

Use an overlap of 0–12 seconds; inputs must match in sample rate and channel count. It does not prove live gapless playback on a device.
