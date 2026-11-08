import PigAPI
import PigBrand
import PigDomain
import UIKit
import SwiftUI

struct PreferencesView: View {
  let model: AppModel
  @State private var preferences = AccountPreferences()
  @State private var loaded = false
  @State private var message: StatusMessage?
  var body: some View {
    Form {
      // Applies at once and is kept on this phone; it syncs whenever it can,
      // so it works offline and during a sync.
      Picker("Appearance", selection: $preferences.appearance) {
        Text("System").tag("system")
        Text("Light").tag("light")
        Text("Dark").tag("dark")
      }
      if let message { StatusText(message) }
    }.pigCanvas().navigationTitle("Appearance").navigationBarTitleDisplayMode(.inline).task {
      preferences = model.preferences
      loaded = true
    }
    .onChange(of: preferences.appearance) { _, appearance in
      guard loaded, appearance != model.preferences.appearance else { return }
      Task { await save(preferences) }
    }
  }
  private func save(_ value: AccountPreferences) async {
    // nil: a newer choice replaced this one, and its own save reports.
    guard let confirmed = await model.choosePreferences(value) else { return }
    message = .info(confirmed ? "Saved." : "Saved on this phone; it will sync when connected.")
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
struct SessionsView: View {
  @Environment(\.colorScheme) private var scheme
  let model: AppModel
  @State private var sessions: [AccountSession] = []
  @State private var loading = false
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
      ForEach(active) { session in
        VStack(alignment: .leading, spacing: 2) {
          HStack(spacing: 6) {
            Text(session.deviceName.isEmpty ? session.kind : session.deviceName)
            if session.current == true {
              Text("This device").font(PigType.caption.weight(.semibold))
                .foregroundStyle(PigBrand.success(scheme))
            }
          }
          Text(
            session.platformName + " · "
              + (session.current == true
                ? "In use now"
                : "Last used "
                  + Date(timeIntervalSince1970: session.lastSeenAt).formatted(
                    .relative(presentation: .named)))
          ).font(PigType.caption).foregroundStyle(.secondary)
          if session.current != true {
            Button("Sign out", role: .destructive) { revoking = session }.disabled(
              loading || !model.canMutate)
          }
        }
      }
      if active.contains(where: { $0.current != true }) {
        Button("Sign out other devices", role: .destructive) { signingOutOthers = true }
          .disabled(loading || !model.canMutate)
      }
      if let message { StatusText(message) }
      if loading { ProgressView() }
    }.pigCanvas().navigationTitle("Devices and sessions").navigationBarTitleDisplayMode(.inline).task { await load() }
      .toolbar {
        Button("Refresh", systemImage: "arrow.clockwise") { Task { await load() } }.disabled(
          loading)
      }
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
  private func load() async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    do { sessions = try await model.readCatalogue { try await $0.sessions() } } catch {
      message = .error(error.userMessage)
    }
  }
}
struct QualityView: View {
  let model: AppModel
  var scope = "system"
  var id = "*"
  @State private var quality: QualitySettings?
  @State private var selected = "inherit"
  @State private var loading = false
  @State private var message: StatusMessage?
  var body: some View {
    Form {
      if let quality {
        Text("Household default: \(quality.householdDefault)").font(PigType.footnote)
        Picker("My preference", selection: $selected) {
          Text("Inherit default").tag("inherit")
          ForEach(quality.options) { option in Text(option.label).tag(option.value) }
        }
        Text(
          "Shared files keep the highest requested quality. This preference affects server acquisition, not the size of downloads to this device."
        ).font(PigType.footnote)
        Button("Save preference") {
          Task {
            loading = true
            defer { loading = false }
            let value = selected == "inherit" ? nil : selected
            do {
              try await model.performPersonal {
                try await $0.saveQuality(scope: scope, id: id, preferred: value)
              }
              message = .info("Preference saved.")
            } catch { message = .error(error.userMessage) }
          }
        }.disabled(loading || !model.canMutate)
      } else {
        ProgressView()
      }
      if let message { StatusText(message) }
    }.pigCanvas().navigationTitle("Server quality").navigationBarTitleDisplayMode(.inline).task {
      do {
        let value = try await model.readCatalogue { try await $0.quality() }
        quality = value
        selected =
          value.preferences.first(where: { $0.scope == scope && $0.itemId == id })?.preferred
          ?? "inherit"
      } catch { message = .error(error.userMessage) }
    }
  }
}
struct RemovalView: View {
  let model: AppModel
  let tracks: [Track]
  @State private var preview: RemovalPreview?
  @State private var result: RemovalResult?
  @State private var loading = false
  @State private var message: StatusMessage?
  @State private var confirming = false
  var body: some View {
    Form {
      if let result {
        Text("Removed \(result.removed) songs from your library.")
        Button("Undo removal") { Task { await undo(result) } }.disabled(
          loading || !model.canMutate || Date().timeIntervalSince1970 > result.undoUntil)
        Text(
          "Undo is available briefly. After it expires, ask the administrator to check Recovery."
        ).font(PigType.footnote)
      } else if let preview {
        Text(
          "\(preview.deleteCount) files have no other holders and will move to Recovery. \(preview.keepCount) remain for other people."
        )
        ForEach(preview.items) { item in
          Text(
            item.deleteFile
              ? "Move file to Recovery" : "Keep file for \(item.keptFor.joined(separator:", "))"
          ).font(PigType.footnote)
        }
        Button("Remove from my library", role: .destructive) { confirming = true }.disabled(
          loading || !model.canMutate
        )
        .confirmationDialog(
          "Confirm removal and the file effects shown above?", isPresented: $confirming
        ) { Button("Remove music", role: .destructive) { Task { await remove() } } }
      } else {
        Button("Preview removal") { Task { await load() } }.disabled(
          loading || !model.canMutate || tracks.count > 500)
      }
      if loading { ProgressView() }
      if let message { StatusText(message) }
    }.pigCanvas().navigationTitle("Remove music").navigationBarTitleDisplayMode(.inline).task { await load() }
  }
  private func load() async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    let ids = tracks.map(\.id)
    do { preview = try await model.readCatalogue { try await $0.removalPreview(ids: ids) } } catch {
      message = .error(error.userMessage)
    }
  }
  private func remove() async {
    guard let preview, !loading else { return }
    loading = true
    defer { loading = false }
    let ids = tracks.map(\.id)
    let token = preview.confirmation
    do {
      result = try await model.performPersonal {
        try await $0.removeMusic(ids: ids, confirmation: token)
      }
    } catch {
      self.preview = nil
      message = .error(error.userMessage + " Preview again before confirming.")
    }
  }
  private func undo(_ result: RemovalResult) async {
    loading = true
    defer { loading = false }
    do {
      try await model.performPersonal { try await $0.undoRemoval(id: result.id) }
      self.result = nil
      preview = nil
      message = .info("Music restored.")
    } catch { message = .error(error.userMessage) }
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
            LabeledContent("Watch app", value: diagnostics.version ?? "Unknown")
            LabeledContent(
              "Watch log", value: diagnostics.sentAt.formatted(.relative(presentation: .named)))
            if diagnostics.endedUnexpectedly {
              Text("The Watch reported that its previous run ended unexpectedly.")
                .font(PigType.footnote).foregroundStyle(PigBrand.danger(scheme))
            }
          }
          Button("Ask the Watch for its log") { watch.requestWatchDiagnostics() }
            .disabled(watch.link?.activated != true)
        }
      }
      Section {
        if let export {
          ShareLink(item: export) { Label("Export diagnostics", systemImage: "square.and.arrow.up") }
        }
      } footer: {
        Text(
          "The export holds app events, connection states and crash call stacks from this iPhone and the Watch. It contains no passwords, sign-in tokens or music links."
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
  static let serverUpdate =
    "This server is older than the app. Household songs in playlists and live request progress need a server update."
  private func makeExport() -> URL? {
    var parts = [
      "PigMusic diagnostics · \(Date().formatted(.iso8601))",
      "iPhone app \(AppVersion.text) · iOS \(UIDevice.current.systemVersion)",
      server.map { "Server \($0.version) (\($0.revision)), API level \($0.apiLevel)" }
        ?? "Server: \(serverChecked ? "older than this app or unreachable" : "not checked")",
      "", "== iPhone log ==", DiagnosticsLog.shared.text(),
    ]
    if let pending = model.refreshFailurePending { parts.append("[sync] \(pending)") }
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
