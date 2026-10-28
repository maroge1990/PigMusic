import MetricKit
import PigBrand
import PigDomain
import PigDownloads
import SwiftUI
import UIKit

@main struct PigMusicApp: App {
  @UIApplicationDelegateAdaptor(PigMusicAppDelegate.self) private var delegate
  @State private var model = AppModel()
  @State private var starting = true
  @Environment(\.scenePhase) private var phase
  var body: some Scene {
    WindowGroup {
      RootView(model: model)
        .overlay { if starting { PigBrandSplash().transition(.opacity) } }
        .task {
          await model.restore()
          finishStarting()
        }
        .onChange(of: model.person?.id) { _, person in
          // A restored account is attached before its first refresh. Keep the
          // splash over the half-filled library until that refresh ends, at most
          // briefly, so no intermediate screen flashes.
          guard person != nil, starting else { return }
          Task {
            try? await Task.sleep(for: .seconds(1.5))
            finishStarting()
          }
        }
        .onChange(of: phase, initial: true) { _, value in
          if value == .active { DiagnosticsLog.shared.foreground() }
          if value == .background { DiagnosticsLog.shared.background() }
          Task { await model.sceneActive(value == .active, background: value == .background) }
        }
    }
  }
}

extension PigMusicApp {
  fileprivate func finishStarting() {
    guard starting else { return }
    withAnimation(.easeOut(duration: 0.25)) { starting = false }
  }
}

struct RootView: View {
  @Bindable var model: AppModel
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    Group {
      if model.person == nil { SignInView(model: model) } else { LibraryShell(model: model) }
    }
    .preferredColorScheme(
      model.preferences.appearance == "dark"
        ? .dark : (model.preferences.appearance == "light" ? .light : nil)
    )
    .tint(PigBrand.accent(scheme))
    .scrollContentBackground(.hidden)
    .background { PigCanvasBackground() }
  }
}

struct SignInView: View {
  @Bindable var model: AppModel
  @State private var username = ""
  @State private var password = ""
  @State private var invitation = ""
  var body: some View {
    NavigationStack {
      Form {
        Section {
          Image("PigLockup").resizable().scaledToFit().frame(maxWidth: 280).frame(height: 100)
            .frame(maxWidth: .infinity)
            .accessibilityHidden(true)
          Text("Your music, wherever you are.").font(PigType.headline)
        }
        Section("Your server") {
          TextField("https://music.example.com", text: $model.server)
            .textContentType(.URL).keyboardType(.URL).textInputAutocapitalization(.never)
            .autocorrectionDisabled()
          Text("Use the address from your invitation, or ask the person who runs your server.")
            .font(PigType.footnote)
        }
        Section("Your account") {
          TextField("Username", text: $username).textContentType(.username)
            .textInputAutocapitalization(.never).autocorrectionDisabled()
          SecureField("Password", text: $password).textContentType(.password)
          Button {
            Task {
              await model.signIn(username: username, password: password)
              password = ""
            }
          } label: {
            HStack {
              Text("Sign in")
              Spacer()
              if model.busy { ProgressView() }
            }
          }.pigPrimaryAction().disabled(
            model.busy || username.isEmpty || password.isEmpty || model.server.isEmpty)
        }
        Section("Join with an invitation") {
          TextField("Invitation link or code", text: $invitation).textInputAutocapitalization(
            .never
          ).autocorrectionDisabled()
          Text("Use the password above for your new account; at least 12 characters.").font(
            PigType.footnote)
          Button("Activate account") {
            Task {
              await model.join(invitation: invitation, password: password)
              password = ""
              invitation = ""
            }
          }
          .disabled(model.busy || invitation.isEmpty || password.count < 12)
        }
        if let message = model.message {
          Section {
            Text(message).foregroundStyle(Color("PigDanger")).accessibilityLabel(
              "Sign-in status: " + message)
          }
        }
      }.navigationTitle("PigMusic").pigCanvas()
    }
  }
}

private enum Destination: String, CaseIterable, Identifiable {
  case home = "Home"
  case library = "Library"
  case downloads = "Downloads"
  case search = "Search"
  var id: String { rawValue }
  var symbol: String {
    switch self {
    case .home: "house"
    case .library: "square.stack"
    case .downloads: "arrow.down.circle"
    case .search: "magnifyingglass"
    }
  }
}

/// Same sections as the web app. Account opens from the avatar, as on the web,
/// and the mini player is integrated with the tab bar like Apple Music.
struct LibraryShell: View {
  @Bindable var model: AppModel
  @State private var selection: Destination = .home
  @State private var showPlayer = false
  @State private var showAccount = false
  /// The player's artist link: pushed onto the selected tab once the player closes.
  @State private var artistRoute: ArtistRoute?
  private var playing: Bool { model.player.queue.current != nil }
  var body: some View {
    tabs
      .sheet(isPresented: $showPlayer) {
        NowPlayingView(model: model) { artistRoute = $0 }
      }
      .sheet(isPresented: $showAccount) {
        NavigationStack {
          AccountView(model: model).pigCanvas()
            .toolbar { Button("Done") { showAccount = false } }
        }
      }
      .sheet(item: $model.actionTrack) {
        // A half-height card like the web's ⋯ menu; drag up for every action.
        SongActionsView(model: model, track: $0.actionTrack)
          .presentationDetents([.medium, .large])
          .presentationDragIndicator(.visible)
          .presentationContentInteraction(.scrolls)
      }
      #if DEBUG
        // Headless UI review: --tab=<name> and --demo-play.
        .task {
          let arguments = ProcessInfo.processInfo.arguments
          if let tab = arguments.first(where: { $0.hasPrefix("--tab=") })?.dropFirst(6),
            let value = Destination.allCases.first(where: { $0.rawValue.lowercased() == tab })
          {
            selection = value
          }
          if arguments.contains("--demo-play") {
            while model.songs.isEmpty { try? await Task.sleep(for: .milliseconds(200)) }
            model.play(model.songs)
            if !arguments.contains("--demo-autoplay") { model.player.pause() }
          }
        }
      #endif
  }
  @ViewBuilder private var tabs: some View {
    let view = TabView(selection: $selection) {
      Tab(Destination.home.rawValue, systemImage: Destination.home.symbol, value: .home) {
        stack(.home)
      }
      Tab(Destination.library.rawValue, systemImage: Destination.library.symbol, value: .library) {
        stack(.library)
      }
      Tab(
        Destination.downloads.rawValue, systemImage: Destination.downloads.symbol,
        value: .downloads
      ) { stack(.downloads) }
        .badge(model.pendingDownloadCount)
      Tab(value: Destination.search, role: .search) { stack(.search) }
    }
    .tabViewStyle(.sidebarAdaptable)
    if #available(iOS 26.1, *) {
      view.tabBarMinimizeBehavior(.onScrollDown)
        .tabViewBottomAccessory(isEnabled: playing) {
          MiniPlayer(model: model) { showPlayer = true }
        }
    } else {
      view
    }
  }
  private var accessoryAvailable: Bool {
    if #available(iOS 26.1, *) { return true }
    return false
  }
  private func stack(_ destination: Destination) -> some View {
    NavigationStack {
      Group {
        switch destination {
        case .home: HomeView(model: model)
        case .library: LibraryView(model: model)
        case .downloads: DownloadsHubView(model: model)
        case .search: SearchView(model: model)
        }
      }
      .pigCanvas()
      .navigationDestination(item: destination == selection ? $artistRoute : .constant(nil)) {
        ArtistRouteView(model: model, route: $0)
      }
      // Older systems: the player docks directly above the tab bar inside each
      // tab, so it never covers the tabs or the end of a list.
      .safeAreaInset(edge: .bottom, spacing: 0) {
        if !accessoryAvailable && playing {
          MiniPlayer(model: model) { showPlayer = true }
            .pigFloatingControls().padding(.horizontal, 12).padding(.bottom, 6)
        }
      }
      .toolbar {
        if destination != .search {
          // The avatar draws its own circle; iOS 26's shared glass capsule would sit behind it.
          if #available(iOS 26.0, *) {
            ToolbarItem(placement: .topBarTrailing) { accountButton }
              .sharedBackgroundVisibility(.hidden)
          } else {
            ToolbarItem(placement: .topBarTrailing) { accountButton }
          }
        }
      }
    }
  }
  private var accountButton: some View {
    Button {
      showAccount = true
    } label: {
      AccountAvatar(name: model.person?.displayName ?? model.person?.username ?? "")
    }.accessibilityLabel("Account")
  }
}

private struct AccountAvatar: View {
  let name: String
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    Text(
      name.split(separator: " ").prefix(2).compactMap(\.first).map(String.init).joined()
        .uppercased()
    )
    .font(.footnote.weight(.bold)).foregroundStyle(PigBrand.textAccent(scheme))
    .frame(width: 34, height: 34)
    .background(PigBrand.accent(scheme).opacity(0.16), in: Circle())
  }
}

/// Pages show where you are in their own content, so the navigation bar never
/// draws a background, a scroll-edge blur or a collapsed title; only the back
/// button and toolbar buttons float over the page.
private struct PigCanvas: ViewModifier {
  func body(content: Content) -> some View {
    let page = content.pigBrandedCanvas()
      .toolbarBackgroundVisibility(.hidden, for: .navigationBar)
      .toolbar {
        ToolbarItem(placement: .principal) { Color.clear.frame(width: 1, height: 1) }
      }
    if #available(iOS 26.0, *) {
      page.scrollEdgeEffectHidden(true, for: .top)
    } else {
      page
    }
  }
}
extension View {
  func pigCanvas() -> some View { modifier(PigCanvas()) }
}

@MainActor final class PigMusicAppDelegate: NSObject, UIApplicationDelegate {
  func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
  ) -> Bool {
    DiagnosticsLog.shared.launched("iPhone", version: AppVersion.text)
    // The system delivers crash reports from earlier runs on a later launch.
    MXMetricManager.shared.add(CrashReports.shared)
    return true
  }
  func application(
    _ application: UIApplication, handleEventsForBackgroundURLSession identifier: String,
    completionHandler: @escaping () -> Void
  ) {
    BackgroundTransferHub.handleEvents(identifier: identifier, completion: completionHandler)
  }
}

enum AppVersion {
  static var text: String {
    let info = Bundle.main.infoDictionary
    let version = info?["CFBundleShortVersionString"] as? String ?? "?"
    let build = info?["CFBundleVersion"] as? String ?? "?"
    return "\(version) (\(build))"
  }
}

/// Crash reports the system delivers through MetricKit, kept on this iPhone
/// (newest five) for the diagnostics export. They hold call stacks, not data.
final class CrashReports: NSObject, MXMetricManagerSubscriber, @unchecked Sendable {
  static let shared = CrashReports()
  private let directory = FileManager.default.urls(
    for: .applicationSupportDirectory, in: .userDomainMask
  ).first!.appendingPathComponent("Diagnostics/Crashes")

  func didReceive(_ payloads: [MXDiagnosticPayload]) {
    try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    for payload in payloads where !(payload.crashDiagnostics ?? []).isEmpty {
      let name = "crash-\(Int(payload.timeStampEnd.timeIntervalSince1970)).json"
      try? payload.jsonRepresentation().write(to: directory.appendingPathComponent(name))
      DiagnosticsLog.shared.record(
        "crash", "crash report received for a run ending \(payload.timeStampEnd)")
    }
    let files = reports()
    for old in files.dropLast(5) { try? FileManager.default.removeItem(at: old) }
  }
  /// Oldest first.
  func reports() -> [URL] {
    ((try? FileManager.default.contentsOfDirectory(
      at: directory, includingPropertiesForKeys: nil)) ?? [])
      .filter { $0.pathExtension == "json" }
      .sorted { $0.lastPathComponent < $1.lastPathComponent }
  }
}
