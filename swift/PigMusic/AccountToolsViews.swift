import PigAPI
import PigBrand
import PigDomain
import UIKit
import SwiftUI

/// Appearance as three small previews, then every playback switch in one
/// place (D-120): Seamless playback and Crossfade moved here from Account.
struct PreferencesView: View {
  let model: AppModel
  @State private var preferences = AccountPreferences()
  @State private var loaded = false
  @State private var message: StatusMessage?
  var body: some View {
    Form {
      // Applies at once and is kept on this phone; it syncs whenever it can,
      // so it works offline and during a sync.
      Section("Appearance") {
        HStack(spacing: 10) {
          ForEach(AppearanceTile.Choice.allCases, id: \.self) { choice in
            AppearanceTile(choice: choice, selected: preferences.appearance == choice.rawValue) {
              preferences.appearance = choice.rawValue
            }
          }
        }.padding(.vertical, 8)
      }
      Section("Playback") {
        ForEach(PlaybackSwitch.shown(seamless: model.player.seamlessEnabled), id: \.self) { item in
          Toggle(item.title, isOn: binding(item))
        }
      }
      if let message { StatusText(message) }
    }.pigCanvas().navigationTitle("Preferences").navigationBarTitleDisplayMode(.inline).task {
      preferences = model.preferences
      loaded = true
    }
    .onChange(of: preferences) { _, value in
      guard loaded, value != model.preferences else { return }
      Task { await save(value) }
    }
  }
  /// Keep playing is the account's and syncs; the other two belong to this
  /// iPhone's player.
  private func binding(_ item: PlaybackSwitch) -> Binding<Bool> {
    switch item {
    case .keepPlaying: $preferences.keepPlaying
    case .seamless:
      Binding(get: { model.player.seamlessEnabled }, set: { model.player.seamlessEnabled = $0 })
    case .crossfade:
      Binding(get: { model.player.crossfadeEnabled }, set: { model.player.crossfadeEnabled = $0 })
    }
  }
  private func save(_ value: AccountPreferences) async {
    // nil: a newer choice replaced this one, and its own save reports.
    guard let confirmed = await model.choosePreferences(value) else { return }
    message = .info(confirmed ? "Saved." : "Saved on this iPhone; it will be sent when you're online.")
  }
}

/// One Appearance choice: a small picture of the app in that look, its name
/// and a round mark when chosen.
private struct AppearanceTile: View {
  enum Choice: String, CaseIterable {
    case system, light, dark
    var title: String { rawValue.capitalized }
  }
  let choice: Choice
  let selected: Bool
  let action: () -> Void
  @Environment(\.colorScheme) private var scheme
  @ScaledMetric(relativeTo: .body) private var width: CGFloat = 64
  var body: some View {
    Button(action: action) {
      VStack(spacing: 8) {
        preview
          .frame(width: width, height: width * 1.55)
          .clipShape(.rect(cornerRadius: 12, style: .continuous))
          .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
              .strokeBorder(
                selected ? PigBrand.accent(scheme) : PigBrand.border(scheme),
                lineWidth: selected ? 3 : 1)
          }
        Image(systemName: selected ? "checkmark.circle.fill" : "circle")
          .foregroundStyle(selected ? PigBrand.accent(scheme) : .secondary)
        Text(choice.title).font(PigType.subhead)
      }
      .frame(maxWidth: .infinity)
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .accessibilityLabel(choice.title)
    .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
  }
  @ViewBuilder private var preview: some View {
    switch choice {
    case .system:
      HStack(spacing: 0) {
        page(.light)
        page(.dark)
      }
    case .light: page(.light)
    case .dark: page(.dark)
    }
  }
  private func page(_ look: ColorScheme) -> some View {
    VStack(alignment: .leading, spacing: 6) {
      RoundedRectangle(cornerRadius: 4).fill(PigBrand.accent(look).opacity(0.6))
        .frame(width: 18, height: 18)
      Capsule().fill(PigBrand.border(look)).frame(height: 5)
      Capsule().fill(PigBrand.border(look)).frame(width: 30, height: 5)
      Spacer(minLength: 0)
    }
    .padding(8)
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    .background(PigBrand.canvas(look))
  }
}
struct PasswordView: View {
  let model: AppModel
  @State private var current = ""
  @State private var password = ""
  @State private var repeatPassword = ""
  @State private var loading = false
  @State private var message: StatusMessage?
  var body: some View {
    Form {
      SecureField("Current password", text: $current).textContentType(.password)
      SecureField("New password", text: $password).textContentType(.newPassword)
      SecureField("Repeat new password", text: $repeatPassword).textContentType(.newPassword)
      Text("Use at least 12 characters. Other sessions are revoked when the password changes.")
        .font(PigType.footnote)
      Button("Change password") {
        Task {
          loading = true
          defer {
            loading = false
            current = ""
            password = ""
            repeatPassword = ""
          }
          let old = current
          let new = password
          do {
            try await model.performPersonal {
              try await $0.changePassword(current: old, password: new)
            }
            message = .info("Password changed.")
          } catch { message = .error(error.userMessage) }
        }
      }.disabled(
        loading || !model.canMutate || current.isEmpty || password.count < 12
          || password != repeatPassword)
      if let message { StatusText(message) }
    }.pigCanvas().navigationTitle("Password").navigationBarTitleDisplayMode(.inline)
  }
}
/// Every signed-in device with its icon, This device first and marked; a
/// device's ⋯ signs it out, and one red Sign out other devices sits at the
/// foot (D-120). Pull down to refresh.
struct SessionsView: View {
  @Environment(\.colorScheme) private var scheme
  let model: AppModel
  @State private var sessions: [AccountSession] = []
  @State private var loading = false
  @State private var loaded = false
  @State private var message: StatusMessage?
  @State private var revoking: AccountSession?
  @State private var signingOutOthers = false
  /// Signed-in sessions, this device first.
  private var active: [AccountSession] {
    sessions.filter { $0.revokedAt == nil }.sorted {
      ($0.current == true ? 0 : 1) < ($1.current == true ? 0 : 1)
    }
  }
  var body: some View {
    List {
      if !loaded && message == nil {
        PigLoading("Loading your devices")
      }
      if !active.isEmpty {
        Section {
          ForEach(active) { session in row(session) }
        }
      }
      if active.contains(where: { $0.current != true }) {
        Section {
          Button("Sign out other devices", role: .destructive) { signingOutOthers = true }
            .frame(maxWidth: .infinity)
            .disabled(loading || !model.canMutate)
        }
      }
      if let message { StatusText(message) }
    }.pigCanvas().navigationTitle("Devices and sessions").navigationBarTitleDisplayMode(.inline)
      .task { await load() }
      .refreshable { await load() }
      .confirmationDialog(
        "Sign out every other browser and app? This device stays signed in.",
        isPresented: $signingOutOthers
      ) {
        Button("Sign out other devices", role: .destructive) {
          Task {
            loading = true
            do {
              let count = try await model.performPersonal { try await $0.revokeOtherSessions() }
              message = .info("Signed out \(count) other device\(count == 1 ? "" : "s").")
            } catch { message = .error(error.userMessage) }
            loading = false
            await load()
          }
        }
      }
      .confirmationDialog(
        "Sign out this device? It will need to sign in again.",
        isPresented: Binding(get: { revoking != nil }, set: { if !$0 { revoking = nil } })
      ) {
        if let session = revoking {
          Button("Sign out", role: .destructive) {
            Task {
              loading = true
              do {
                try await model.performPersonal { try await $0.revokeSession(id: session.id) }
              } catch { message = .error(error.userMessage) }
              revoking = nil
              loading = false
              await load()
            }
          }
        }
      }
  }
  private func row(_ session: AccountSession) -> some View {
    let name = session.deviceName.isEmpty ? session.platformName : session.deviceName
    return HStack(spacing: 12) {
      // The shared thumb, as Changes not sent yet's rows (#283).
      SymbolThumb(systemImage: Self.symbol(session))
      VStack(alignment: .leading, spacing: 3) {
        HStack(spacing: 8) {
          Text(name).font(.body.weight(.semibold)).lineLimit(1)
          if session.current == true {
            Text("This device").font(PigType.caption.weight(.semibold))
              .foregroundStyle(PigBrand.success(scheme))
              .padding(.horizontal, 8).padding(.vertical, 2)
              .background(PigBrand.success(scheme).opacity(0.12), in: Capsule())
          }
        }
        Text(
          session.platformName + " · "
            + (session.current == true
              ? "In use now"
              : "Last used "
                + Date(timeIntervalSince1970: session.lastSeenAt).formatted(
                  .relative(presentation: .named)))
        ).font(PigType.footnote).foregroundStyle(.secondary)
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .accessibilityElement(children: .combine)
      if session.current != true {
        Menu {
          Button("Sign out", systemImage: "rectangle.portrait.and.arrow.right", role: .destructive) {
            revoking = session
          }.disabled(loading || !model.canMutate)
        } label: {
          Image(systemName: "ellipsis").frame(width: 44, height: 44).foregroundStyle(.secondary)
        }
        .buttonStyle(.borderless).accessibilityLabel("More actions for \(name)")
      }
    }
  }
  /// The device's kind as an icon: iPhone or iPad app, Apple Watch app or a
  /// browser.
  private static func symbol(_ session: AccountSession) -> String {
    switch session.platform {
    case "watchos": "applewatch"
    case "web": "globe"
    default: session.deviceName.lowercased().contains("ipad") ? "ipad" : "iphone"
    }
  }
  private func load() async {
    guard !loading else { return }
    loading = true
    defer {
      loading = false
      loaded = true
    }
    do {
      sessions = try await model.readCatalogue { try await $0.sessions() }
      message = nil
    } catch { message = .error(error.userMessage) }
  }
}
/// Your music quality as a list with a tick (D-120): the household default
/// first, saying its quality in words, then each quality. A tap saves it;
/// there is no Save button.
struct QualityView: View {
  let model: AppModel
  var scope = "system"
  var id = "*"
  /// Told the confirmed choice's words, so Account's row shows what the
  /// tick shows.
  var saved: (String?) -> Void = { _ in }
  /// The choices, the tick and the confirmed choice; a read that started
  /// before a save is dropped (Codex round 1 on batch E).
  @State private var pick = QualityPick()
  @State private var message: StatusMessage?
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    Form {
      if let choices = pick.choices {
        Section("Quality") {
          ForEach(choices.rows) { row in
            Button {
              Task { await choose(row.value) }
            } label: {
              HStack {
                Text(row.title).foregroundStyle(PigBrand.text(scheme))
                Spacer()
                if let detail = row.detail {
                  Text(detail).foregroundStyle(.secondary)
                }
                Image(systemName: "checkmark").foregroundStyle(PigBrand.accent(scheme))
                  .opacity(pick.selected == row.value ? 1 : 0)
              }.contentShape(Rectangle())
            }
            .disabled(pick.saving || !model.canMutate)
            .accessibilityLabel(row.detail.map { row.title + ", " + $0 } ?? row.title)
            .accessibilityAddTraits(
              pick.selected == row.value ? [.isButton, .isSelected] : .isButton)
          }
        }
      } else if message == nil {
        PigLoading("Loading your music quality")
      }
      if let message { StatusText(message) }
    }.pigCanvas().navigationTitle("Your music quality").navigationBarTitleDisplayMode(.inline)
      .task { await load() }
      .refreshable { await load() }
  }
  private func load() async {
    let token = pick.readToken()
    do {
      let value = QualityChoices(
        try await model.readCatalogue { try await $0.quality() }, scope: scope, id: id)
      pick.read(value, token: token)
      message = nil
    } catch { message = .error(error.userMessage) }
  }
  /// Saves at once; a choice the server refuses goes back to the last one.
  private func choose(_ value: String?) async {
    guard pick.startSave(value) else { return }
    do {
      try await model.performPersonal {
        try await $0.saveQuality(scope: scope, id: id, preferred: value)
      }
      pick.finishSave(succeeded: true)
      message = nil
    } catch {
      pick.finishSave(succeeded: false)
      message = .error(error.userMessage)
    }
    saved(pick.summary)
  }
}

/// Versions, the Watch connection, and an export of both devices' lifecycle
/// logs and any crash reports, for diagnosing crashes and stuck transfers.
struct DiagnosticsView: View {
  @Environment(\.colorScheme) private var scheme
  let model: AppModel
  @State private var server: ServerInfo?
  @State private var serverChecked = false
  @State private var serverError: String?
  @State private var export: URL?
  var body: some View {
    Form {
      Section("This iPhone") {
        LabeledContent("PigMusic", value: AppVersion.text)
        LabeledContent("Crash reports", value: CrashReports.shared.reports().count.formatted())
        if DiagnosticsLog.shared.previousRunEndedUnexpectedly {
          Text("The last run on this iPhone ended unexpectedly.")
            .font(PigType.footnote).foregroundStyle(PigBrand.danger(scheme))
        }
      }
      Section("Server") {
        // Moved here from Account's header (D-120).
        LabeledContent("Address") {
          Text(model.server).textSelection(.enabled)
        }
        if let server {
          LabeledContent("Version", value: server.version)
          LabeledContent("Build", value: server.revision)
          if server.apiLevel < ServerInfo.expectedLevel {
            Text(Self.serverUpdate).font(PigType.footnote).foregroundStyle(PigBrand.danger(scheme))
          }
        } else if serverChecked {
          Text(serverError ?? Self.serverUpdate).font(PigType.footnote)
            .foregroundStyle(.secondary)
        } else {
          ProgressView()
        }
      }
      if let watch = model.watch, watch.supported {
        Section("Apple Watch") {
          LabeledContent("Connection", value: watch.link?.activated == true ? "Active" : "Not active")
          LabeledContent("Paired", value: watch.link?.paired == true ? "Yes" : "No")
          LabeledContent("PigMusic installed", value: watch.link?.installed == true ? "Yes" : "No")
          LabeledContent("Nearby", value: watch.link?.reachable == true ? "Yes" : "No")
          if let confirmed = watch.confirmedAt {
            LabeledContent(
              "Last confirmed", value: confirmed.formatted(.relative(presentation: .named)))
          }
          if let diagnostics = watch.watchDiagnostics {
            LabeledContent("Apple Watch app", value: diagnostics.version ?? "Unknown")
            LabeledContent(
              "Apple Watch log", value: diagnostics.sentAt.formatted(.relative(presentation: .named)))
            if diagnostics.endedUnexpectedly {
              Text("Apple Watch reported that its previous run ended unexpectedly.")
                .font(PigType.footnote).foregroundStyle(PigBrand.danger(scheme))
            }
          }
          Button("Ask Apple Watch for its log") { watch.requestWatchDiagnostics() }
            .disabled(watch.link?.activated != true)
        }
      }
      Section {
        if let export {
          ShareLink(item: export) { Label("Export diagnostics", systemImage: "square.and.arrow.up") }
        }
      } footer: {
        Text(
          "The export holds app events, connection states, battery figures and crash call stacks from this iPhone and Apple Watch. It contains no passwords, sign-in tokens or music links."
        )
      }
    }
    .pigCanvas().navigationTitle("Diagnostics").navigationBarTitleDisplayMode(.inline)
    .task {
      export = makeExport()
      do {
        server = try await model.readCatalogue { try await $0.serverInfo() }
      } catch { serverError = error.userMessage }
      serverChecked = true
      export = makeExport()
    }
    .onChange(of: model.watch?.watchDiagnostics?.sentAt) { export = makeExport() }
  }
  static let serverUpdate = "Update the server to use everything"
  private func makeExport() -> URL? {
    var parts = [
      "PigMusic diagnostics · \(Date().formatted(.iso8601))",
      "iPhone app \(AppVersion.text) · iOS \(UIDevice.current.systemVersion)",
      server.map { "Server \($0.version) (\($0.revision)), API level \($0.apiLevel)" }
        ?? "Server: \(serverChecked ? "older than this app or unreachable" : "not checked")",
      "", "== iPhone log ==", DiagnosticsLog.shared.text(),
    ]
    if let pending = model.refreshFailurePending { parts.append("[sync] \(pending)") }
    if let watch = model.watch, watch.supported {
      parts += ["", "== Watch delivery ==", watch.deliveryLog.summary()]
      parts += ["", "== Watch copies in the background ==", watch.backgroundWork.summary()]
    }
    // What the app's work cost the battery, as iOS measures it (D-113).
    let battery = BatteryReports.shared.reports()
    parts += ["", "== Battery and background use (MetricKit, about a day per report) =="]
    if battery.isEmpty {
      parts.append("No report yet: iOS sends one about once a day, for the day before.")
    }
    for report in battery { parts += [report.text(), ""] }
    if let watch = model.watch?.watchDiagnostics {
      parts += [
        "", "== Watch log (\(watch.version ?? "unknown"), sent \(watch.sentAt.formatted(.iso8601))) ==",
        watch.text ?? "",
      ]
    }
    for report in CrashReports.shared.reports() {
      parts += [
        "", "== \(report.lastPathComponent) ==",
        (try? String(contentsOf: report, encoding: .utf8)) ?? "",
      ]
    }
    let file = FileManager.default.temporaryDirectory.appendingPathComponent(
      "PigMusic-diagnostics.txt")
    do {
      try parts.joined(separator: "\n").write(to: file, atomically: true, encoding: .utf8)
      return file
    } catch { return nil }
  }
}
