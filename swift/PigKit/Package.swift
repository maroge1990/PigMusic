// swift-tools-version: 6.0
import PackageDescription

let package = Package(
  name: "PigKit",
  platforms: [.iOS("26.0"), .watchOS(.v11), .macOS(.v13)],
  products: [
    .library(name: "PigBrand", targets: ["PigBrand"]),
    .library(name: "PigAPI", targets: ["PigAPI"]),
    .library(name: "PigDomain", targets: ["PigDomain"]),
    .library(name: "PigStore", targets: ["PigStore"]),
    .library(name: "PigSync", targets: ["PigSync"]),
    .library(name: "PigDownloads", targets: ["PigDownloads"]),
    .library(name: "PigPlayback", targets: ["PigPlayback"]),
    .library(name: "PigCompanion", targets: ["PigCompanion"]),
    .executable(name: "PigAudioProbe", targets: ["PigAudioProbe"]),
    .executable(name: "PigPlaybackHarness", targets: ["PigPlaybackHarness"]),
  ],
  dependencies: [
    .package(url: "https://github.com/apple/swift-http-types", from: "1.0.0"),
    .package(url: "https://github.com/apple/swift-openapi-generator", from: "1.0.0"),
    .package(url: "https://github.com/apple/swift-openapi-runtime", from: "1.0.0"),
    .package(url: "https://github.com/apple/swift-openapi-urlsession", from: "1.0.0"),
    .package(url: "https://github.com/groue/GRDB.swift", from: "7.0.0"),
  ],
  targets: [
    .target(name: "PigBrand"),
    .target(
      name: "PigAPI",
      dependencies: [
        .product(name: "HTTPTypes", package: "swift-http-types"),
        .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
        .product(name: "OpenAPIURLSession", package: "swift-openapi-urlsession"),
      ],
      plugins: [
        .plugin(name: "OpenAPIGenerator", package: "swift-openapi-generator")
      ]),
    .target(name: "PigDomain"),
    .target(
      name: "PigStore", dependencies: ["PigDomain", .product(name: "GRDB", package: "GRDB.swift")]),
    .target(name: "PigSync", dependencies: ["PigDomain", "PigAPI", "PigStore"]),
    .target(name: "PigDownloads", dependencies: ["PigDomain"]),
    .target(name: "PigPlayback", dependencies: ["PigDomain"]),
    .target(name: "PigCompanion", dependencies: ["PigDomain"]),
    .executableTarget(name: "PigAudioProbe", dependencies: ["PigPlayback"]),
    .executableTarget(
      name: "PigPlaybackHarness", dependencies: ["PigAPI", "PigDomain", "PigSync", "PigPlayback"]),
    .testTarget(
      name: "PigCoreTests",
      dependencies: [
        "PigDomain", "PigStore", "PigSync", "PigPlayback", "PigDownloads", "PigCompanion",
      ]),
    .testTarget(name: "PigAPITests", dependencies: ["PigAPI", "PigDomain", "PigStore", "PigSync"]),
  ]
)
