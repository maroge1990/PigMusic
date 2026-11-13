import PigBrand
import PigDomain
import PigDownloads
import SwiftUI

struct DownloadsView: View {
  let model: AppModel
  var body: some View {
    Group {
      if let downloads = model.downloads {
        DownloadList(model: model, downloads: downloads)
      } else {
        // Only between sign-in and the library opening, a moment at most.
        ProgressView("Opening your downloads…").frame(maxWidth: .infinity, maxHeight: .infinity)
      }
    }.pigCanvas().navigationTitle("On this device").navigationBarTitleDisplayMode(.inline)
  }
}
private struct DownloadList: View {
  let model: AppModel
  @Bindable var downloads: DownloadController
  @State private var budget = 5
  @State private var clearing = false
  @State private var removing: DeviceGroup?
  var body: some View {
    let transfers = downloads.records.filter { $0.state != "ready" || $0.cleared != true }
    let groups = DeviceGroup.build(downloads.records.filter { $0.state == "ready" })
    List {
      if downloads.records.isEmpty {
        PigQuietState(
          "No downloads yet",
          line: "Songs, albums and playlists you download to this iPhone appear here."
        ).listRowBackground(Color.clear)
      }
      if !transfers.isEmpty {
        Section("Transfers") {
          ForEach(transfers) { record in
            TransferRow(model: model, downloads: downloads, record: record)
          }
        }
      }
      if !groups.isEmpty {
        Section("On this device") {
          ForEach(groups) { group in
            DisclosureGroup {
              ForEach(Array(group.records.enumerated()), id: \.element.id) { index, record in
                HStack(spacing: 10) {
                  Button {
                    model.play(group.records.map(\.track), at: index)
                  } label: {
                    VStack(alignment: .leading, spacing: 2) {
                      Text(record.track.title).lineLimit(1)
                      Text(record.track.artist).font(PigType.footnote).foregroundStyle(.secondary)
                        .lineLimit(1)
                    }.frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                  }.buttonStyle(.plain)
                  RemoveButton(label: "Remove \(record.track.title) from this device") {
                    Task { await downloads.release(ids: [record.id], origin: group.origin) }
                  }
                }
              }
            } label: {
              CollectionRow(group.title, subtitle: group.caption) {
                ArtworkThumb(model: model, source: group.artwork, title: group.title)
              } trailing: {
                RemoveButton(label: "Remove \(group.title) from this device") { removing = group }
              }
            }
          }
        }
      }
      Section("Storage") {
        Text(
          "\(ByteCountFormatter.string(fromByteCount:downloads.records.filter{$0.state == "ready"}.reduce(0){$0+$1.total},countStyle:.file)) downloaded"
        )
        Picker("Download budget", selection: $budget) {
          ForEach([1, 2, 5, 10, 20, 50], id: \.self) { Text("\($0) GB").tag($0) }
        }
        .onChange(of: budget) { _, value in
          downloads.budgetBytes = Int64(value) * 1024 * 1024 * 1024
          model.saveDownloadBudget(downloads.budgetBytes)
        }
        if downloads.usesBackgroundTransfers {
          Toggle(
            "Allow cellular downloads",
            isOn: Binding(
              get: { downloads.allowsCellular },
              set: { value in Task { await downloads.setCellular(value) } }))
        }
        Text(
          (downloads.allowsCellular
            ? "Downloads can use Wi-Fi and cellular (not while Low Data Mode is on). "
            : "Downloads use Wi-Fi. ")
            + "Originals are the default; AAC copies use less storage. Use AAC for originals your device cannot decode. Scheduled file transfers continue in the background. Preparing new downloads and AAC copies resumes when the app opens. Access changes reconcile when you next connect."
        ).font(PigType.footnote)
        if downloads.running || downloads.records.contains(where: { $0.state == "downloading" }) {
          Button("Pause downloads") { Task { await downloads.pause() } }
        } else {
          Button("Resume queued downloads") { downloads.resume() }
        }
        if downloads.records.contains(where: \.isPaused) {
          Text("Songs you paused one at a time stay paused until you resume them.")
            .font(PigType.footnote).foregroundStyle(.secondary)
        }
        Button("Remove all device downloads", role: .destructive) { clearing = true }
      }
    }
    .toolbar {
      Button("Clear finished") { Task { await downloads.clearFinished() } }
        .disabled(!transfers.contains { $0.state == "ready" })
    }
    .task {
      await downloads.reload()
      budget = Int(downloads.budgetBytes / (1024 * 1024 * 1024))
    }
    .confirmationDialog(
      "Remove all music downloaded to this device? Server music is retained.",
      isPresented: $clearing
    ) { Button("Remove device downloads", role: .destructive) { Task { await downloads.clear() } } }
    .confirmationDialog(
      removing.map { "Remove \($0.title) from this device?" } ?? "",
      isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
      titleVisibility: .visible
    ) {
      if let group = removing {
        Button("Remove", role: .destructive) {
          Task { await downloads.release(ids: group.records.map(\.id), origin: group.origin) }
        }
      }
    } message: {
      Text("Songs also in another downloaded album or playlist stay on this device.")
    }
  }
}

/// One transfer, shown by its mark: clock (queued), pause (paused on its own),
/// progress ring, check, or a warning that retries. Swipe or touch and hold to
/// pause or resume the song, or cancel it (#127).
private struct TransferRow: View {
  let model: AppModel
  @Bindable var downloads: DownloadController
  let record: DownloadRecord
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    HStack(spacing: 12) {
      ArtworkThumb(model: model, source: record.track.artworkSource, title: record.track.title)
      VStack(alignment: .leading, spacing: 2) {
        Text(record.track.title).lineLimit(1)
        Text(record.track.artist).font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
      }
      Spacer(minLength: 0)
      mark.frame(width: 28, height: 28)
    }
    .accessibilityElement(children: .combine)
    .accessibilityValue(stateLabel)
    .swipeActions {
      if record.state != "ready" {
        Button("Cancel", role: .destructive) { Task { await downloads.remove(id: record.id) } }
      }
      pauseButton.tint(PigBrand.secondary(scheme))
    }
    .contextMenu {
      pauseButton
      if record.state != "ready" {
        Button("Cancel download", systemImage: "xmark", role: .destructive) {
          Task { await downloads.remove(id: record.id) }
        }
      }
    }
  }
  @ViewBuilder private var pauseButton: some View {
    if record.isPaused {
      Button("Resume", systemImage: "play.fill") {
        Task { await downloads.resume(id: record.id) }
      }
    } else if ["queued", "downloading"].contains(record.state) {
      Button("Pause", systemImage: "pause.fill") {
        Task { await downloads.pause(id: record.id) }
      }
    }
  }
  @ViewBuilder private var mark: some View {
    if record.isPaused {
      Image(systemName: "pause.circle").foregroundStyle(.secondary)
    } else {
      stateMark
    }
  }
  @ViewBuilder private var stateMark: some View {
    switch record.state {
    case "ready":
      Image(systemName: "checkmark.circle.fill").foregroundStyle(PigBrand.accent(scheme))
    case "downloading":
      ProgressView(value: downloads.progress[record.id] ?? 0).progressViewStyle(.circular)
        .controlSize(.small).tint(PigBrand.accent(scheme))
    case "failed":
      Button {
        Task {
          try? await downloads.enqueue(
            [record.track], quality: record.quality,
            origin: record.effectiveOrigins.last ?? .direct)
        }
      } label: {
        Image(systemName: "exclamationmark.arrow.circlepath")
          .foregroundStyle(PigBrand.danger(scheme))
      }.buttonStyle(.borderless).accessibilityLabel("Retry")
    default:
      Image(systemName: "clock").foregroundStyle(.secondary)
    }
  }
  private var stateLabel: String {
    if record.isPaused { return "Paused" }
    return switch record.state {
    case "ready": "Downloaded"
    case "downloading": "Downloading"
    case "failed": "Failed: \(record.error ?? "try again")"
    default: "Waiting"
    }
  }
}

struct RemoveButton: View {
  let label: String
  let action: () -> Void
  var body: some View {
    Button(action: action) {
      Image(systemName: "xmark.circle.fill").font(.title3).foregroundStyle(.secondary)
        .frame(width: 32, height: 32).contentShape(Rectangle())
    }
    .buttonStyle(.borderless).accessibilityLabel(label)
  }
}

/// Downloads grouped the way they were added: by album, or by playlist. A song
/// added both ways appears in both groups.
struct DeviceGroup: Identifiable {
  let id: String
  let title: String
  let caption: String
  let origin: DownloadOrigin
  let artwork: AppModel.ArtworkSource?
  let records: [DownloadRecord]
  static func build(_ ready: [DownloadRecord]) -> [DeviceGroup] {
    var playlists: [String: (name: String, records: [DownloadRecord])] = [:]
    var albums: [String: [DownloadRecord]] = [:]
    for record in ready {
      for origin in record.effectiveOrigins {
        if let id = origin.playlistId {
          playlists[id, default: (origin.playlistName ?? "Playlist", [])].records.append(record)
        } else {
          let track = record.track
          let key = track.albumId ?? "\(track.albumArtist ?? track.artist)|\(track.album ?? "")"
          albums[key, default: []].append(record)
        }
      }
    }
    let playlistGroups = playlists.map { id, value in
      DeviceGroup(
        id: "playlist:" + id, title: value.name,
        caption: caption("Playlist", value.records),
        origin: DownloadOrigin(playlistId: id, playlistName: value.name),
        artwork: value.records.lazy.compactMap(\.track.artworkSource).first,
        records: value.records)
    }
    let albumGroups = albums.map { key, records in
      let sorted = records.sorted {
        ($0.track.discNumber ?? 1, $0.track.trackNumber ?? 0, $0.track.title)
          < ($1.track.discNumber ?? 1, $1.track.trackNumber ?? 0, $1.track.title)
      }
      let first = sorted[0].track
      return DeviceGroup(
        id: "album:" + key, title: first.album.flatMap { $0.isEmpty ? nil : $0 } ?? first.title,
        caption: caption(first.albumArtist ?? first.artist, sorted), origin: .direct,
        artwork: sorted.lazy.compactMap(\.track.artworkSource).first, records: sorted)
    }
    func order(_ groups: [DeviceGroup]) -> [DeviceGroup] {
      groups.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
    }
    return order(playlistGroups) + order(albumGroups)
  }
  private static func caption(_ lead: String, _ records: [DownloadRecord]) -> String {
    let size = ByteCountFormatter.string(
      fromByteCount: records.reduce(0) { $0 + $1.total }, countStyle: .file)
    return "\(lead) · \(records.count) song\(records.count == 1 ? "" : "s") · \(size)"
  }
}

/// The Downloads tab: what you asked the server, this iPhone and Apple Watch
/// to download. Each row's badge counts its queued and in-progress songs; the
/// tab badge is their total.
struct DownloadsHubView: View {
  let model: AppModel
  var body: some View {
    List {
      row("Server", detail: "Music you asked the server to get", symbol: "server.rack",
        active: model.serverActiveRequests) { RequestsView(model: model) }
      row("iPhone", detail: phoneSummary, symbol: "iphone", active: model.phoneActiveDownloads) {
        DownloadsView(model: model)
      }
      if model.watch?.supported == true {
        row("Apple Watch", detail: watchSummary, symbol: "applewatch",
          active: model.watchActiveDownloads) { WatchDownloadsView(model: model) }
      }
    }
    .navigationTitle("Downloads")
    .task {
      // Server requests have no live progress feed; poll while any are active.
      await model.refreshServerRequests()
      while model.serverActiveRequests > 0, !Task.isCancelled {
        try? await Task.sleep(for: .seconds(10))
        await model.refreshServerRequests()
      }
    }
  }
  private func row<Destination: View>(
    _ title: String, detail: String, symbol: String, active: Int,
    @ViewBuilder destination: @escaping () -> Destination
  ) -> some View {
    NavigationLink(destination: destination) {
      Label {
        VStack(alignment: .leading) {
          Text(title)
          Text(detail).font(PigType.footnote).foregroundStyle(.secondary)
        }
      } icon: {
        Image(systemName: symbol)
      }
    }.badge(active)
  }
  private var phoneSummary: String {
    let ready = model.downloads?.records.count { $0.state == "ready" } ?? 0
    return "\(ready) songs ready offline"
  }
  private var watchSummary: String {
    guard let receipt = model.watch?.receipt else { return "Transfers wait for your Watch" }
    return "\(receipt.ready.count) songs on Watch"
  }
}
