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
    }.pigCanvas().navigationTitle("iPhone").navigationBarTitleDisplayMode(.inline)
  }
}
/// Transfers, then what is on this iPhone, then its settings: the same
/// sections as the Server and Watch pages, with no explanatory text (#209,
/// D-101).
private struct DownloadList: View {
  let model: AppModel
  @Bindable var downloads: DownloadController
  @State private var budget = 5
  @State private var clearing = false
  var body: some View {
    // A cancel waiting for its Undo has left the list already (#183).
    let transfers = downloads.records.filter {
      ($0.state != "ready" || $0.cleared != true)
        && !model.undo.hides(AppModel.undoKey(download: $0.id))
    }
    let groups = DeviceGroup.build(downloads.records.filter { $0.state == "ready" })
    List {
      if downloads.records.isEmpty {
        Text("No downloads yet").foregroundStyle(.secondary)
      }
      if !transfers.isEmpty {
        Section("Transfers") {
          ForEach(transfers) { record in
            TransferRow(model: model, downloads: downloads, record: record)
          }
          if transfers.contains(where: { $0.state != "ready" }) {
            if downloads.running || downloads.records.contains(where: { $0.state == "downloading" }) {
              Button("Pause downloads") { Task { await downloads.pause() } }
            } else {
              Button("Resume downloads") { downloads.resume() }
            }
          }
        }
      }
      DeviceGroupsSection(
        model: model, title: "On your phone", device: "iPhone", groups: groups,
        play: { group, index in model.play(group.records.map(\.track), at: index) }
      ) { ids, origin in await downloads.release(ids: ids, origin: origin) }
      Section("Settings") {
        if downloads.usesBackgroundTransfers {
          Toggle(
            "Allow cellular downloads",
            isOn: Binding(
              get: { downloads.allowsCellular },
              set: { value in Task { await downloads.setCellular(value) } }))
        }
        Picker("Download budget", selection: $budget) {
          ForEach([1, 2, 5, 10, 20, 50], id: \.self) { Text("\($0) GB").tag($0) }
        }
        .onChange(of: budget) { _, value in
          downloads.budgetBytes = Int64(value) * 1024 * 1024 * 1024
          model.saveDownloadBudget(downloads.budgetBytes)
        }
        Button("Remove all", role: .destructive) { clearing = true }
          .disabled(downloads.records.isEmpty)
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
    .confirmationDialog("Remove all downloads from this iPhone?", isPresented: $clearing) {
      Button("Remove all", role: .destructive) { Task { await downloads.clear() } }
    }
  }
}

/// "On your phone" and "On your watch" (#209): albums and playlists, laid
/// out the same on both pages. Swipe a group or one of its songs to remove
/// it, at once with Undo (D-79).
struct DeviceGroupsSection: View {
  let model: AppModel
  let title: String
  /// "iPhone" or "Apple Watch", for VoiceOver and the Undo label.
  let device: String
  let groups: [DeviceGroup]
  /// Plays a group from one of its songs; nil where songs can't play here.
  var play: ((DeviceGroup, Int) -> Void)?
  let release: @MainActor ([String], DownloadOrigin) async -> Void
  private func key(_ group: DeviceGroup, _ record: DownloadRecord? = nil) -> String {
    "device-remove:" + device + ":" + group.id + (record.map { "/" + $0.id } ?? "")
  }
  var body: some View {
    let shown = groups.filter { !model.undo.hides(key($0)) }
    if !shown.isEmpty {
      Section(title) {
        ForEach(shown) { group in
          DisclosureGroup {
            let records = group.records.filter { !model.undo.hides(key(group, $0)) }
            ForEach(Array(records.enumerated()), id: \.element.id) { index, record in
              songRow(group, record, index: index)
                .swipeActions {
                  Button("Remove", role: .destructive) { remove(group, records: [record]) }
                }
            }
          } label: {
            CollectionRow(group.title, subtitle: group.caption) {
              ArtworkThumb(model: model, source: group.artwork, title: group.title)
            }
          }
          .swipeActions {
            Button("Remove", role: .destructive) { remove(group, records: group.records) }
          }
        }
      }
    }
  }
  @ViewBuilder private func songRow(_ group: DeviceGroup, _ record: DownloadRecord, index: Int)
    -> some View
  {
    let label = VStack(alignment: .leading, spacing: 2) {
      Text(record.track.title).lineLimit(1)
      Text(record.track.artist).font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
    }.frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
    if let play {
      Button { play(group, index) } label: { label }.buttonStyle(.plain)
    } else {
      label
    }
  }
  private func remove(_ group: DeviceGroup, records: [DownloadRecord]) {
    let whole = records.count == group.records.count
    let id = whole ? key(group) : key(group, records[0])
    let ids = records.map(\.id)
    let origin = group.origin
    let release = release
    model.undo.schedule(
      id, label: "Removed " + (whole ? group.title : records[0].track.title) + " from " + device
    ) {
      await release(ids, origin)
      return true
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
        // Said in words as well as by the red mark; the mark retries.
        if record.state == "failed", !record.isPaused { StatusText("Couldn't download", kind: .error) }
      }
      Spacer(minLength: 0)
      mark.frame(width: 28, height: 28)
    }
    .accessibilityElement(children: .combine)
    .accessibilityValue(stateLabel)
    .swipeActions {
      if record.state != "ready" {
        Button("Cancel", role: .destructive) { cancel() }
      }
      pauseButton.tint(PigBrand.secondary(scheme))
    }
    .contextMenu {
      pauseButton
      if record.state != "ready" {
        Button("Cancel download", systemImage: "xmark", role: .destructive) { cancel() }
      }
    }
  }
  /// At once, with Undo (D-79): the transfer stops when the Undo runs out.
  private func cancel() {
    let downloads = downloads
    let id = record.id
    model.undo.schedule(
      AppModel.undoKey(download: id), label: "Cancelled " + record.track.title
    ) {
      await downloads.remove(id: id)
      return true
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
    case "failed": "Couldn't download"
    default: "Waiting"
    }
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
/// to download. Each row says how many songs it holds (#209) and its badge
/// counts its queued and in-progress songs; the tab badge is their total.
struct DownloadsHubView: View {
  let model: AppModel
  var body: some View {
    List {
      row("Server", detail: DownloadPlace.server.summary(songs: model.songCount),
        symbol: "server.rack", active: model.serverActiveRequests) { RequestsView(model: model) }
      row("iPhone", detail: DownloadPlace.phone.summary(songs: phoneSongs), symbol: "iphone",
        active: model.phoneActiveDownloads) { DownloadsView(model: model) }
      if model.watch?.supported == true {
        row("Apple Watch", detail: DownloadPlace.watch.summary(songs: watchSongs),
          symbol: "applewatch", active: model.watchActiveDownloads) { WatchDownloadsView(model: model) }
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
          Text(detail).font(PigType.footnote).foregroundStyle(.secondary).pigFigures()
        }
      } icon: {
        Image(systemName: symbol)
      }
    }.badge(active)
  }
  private var phoneSongs: Int { model.downloads?.records.count { $0.state == "ready" } ?? 0 }
  private var watchSongs: Int { model.watch?.receipt?.ready.count ?? 0 }
}
