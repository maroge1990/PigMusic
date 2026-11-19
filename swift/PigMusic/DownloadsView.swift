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
        PigQuietState(
          "No downloads yet"
        ).listRowBackground(Color.clear)
      }
      if !transfers.isEmpty {
        Section("Transfers") {
          // By album or playlist, as on the Server page (D-109).
          ForEach(DownloadGrouping.transfers(transfers) { $0.state == "ready" }) { group in
            DisclosureGroup {
              ForEach(group.records) { record in
                TransferRow(model: model, downloads: downloads, record: record)
              }
            } label: {
              let control = TransferGroupControl(
                group.records.map {
                  ($0.id, TransferControl.phone($0, progress: downloads.progress[$0.id]))
                })
              TransferGroupLabel(
                model: model, title: group.title,
                subtitle: group.lead + " · " + control.summary(done: "on this iPhone"),
                artwork: group.artwork, control: control, place: .phone, noun: group.noun
              ) { tap, ids in act(tap, on: group.records.filter { ids.contains($0.id) }) }
            }
          }
          if transfers.contains(where: { $0.state != "ready" }) {
            if downloads.running || downloads.records.contains(where: { $0.state == "downloading" }) {
              Button("Pause downloads", systemImage: "pause.circle") {
                Task { await downloads.pause() }
              }
            } else {
              Button("Resume downloads", systemImage: "arrow.down.circle") { downloads.resume() }
            }
          }
        }
      }
      DeviceGroupsSection(
        model: model, title: "On this iPhone", device: "iPhone", groups: groups,
        play: { tracks, index in model.play(tracks, at: index) }
      ) { ids, origin in await downloads.release(ids: ids, origin: origin) }
      Section("Settings") {
        if downloads.usesBackgroundTransfers {
          Toggle(
            "Allow cellular downloads",
            isOn: Binding(
              get: { downloads.allowsCellular },
              set: { value in Task { await downloads.setCellular(value) } }))
        }
        Picker("Storage limit", selection: $budget) {
          ForEach([1, 2, 5, 10, 20, 50], id: \.self) { Text("\($0) GB").tag($0) }
        }
        .onChange(of: budget) { _, value in
          downloads.budgetBytes = Int64(value) * 1024 * 1024 * 1024
          model.saveDownloadBudget(downloads.budgetBytes)
        }
        // A removal that failed shows here, where it was started (#128).
        if let message = model.message {
          StatusText(message)
        }
        Button("Remove all", systemImage: "minus.circle", role: .destructive) { clearing = true }
          .disabled(downloads.records.isEmpty)
      }
    }
    .toolbar {
      ClearFinishedButton(enabled: transfers.contains { $0.state == "ready" }) {
        Task { await downloads.clearFinished() }
      }
    }
    .task {
      await downloads.reload()
      budget = Int(downloads.budgetBytes / (1024 * 1024 * 1024))
    }
    // A centred alert, not Undo: it removes every song (D-114).
    .alert("Remove all songs from this iPhone?", isPresented: $clearing) {
      Button("Cancel", role: .cancel) {}
      Button("Remove all", role: .destructive) { Task { await downloads.clear() } }
    } message: {
      Text("This can't be undone.")
    }
  }
  /// An album's mark (D-110): pause, resume or retry these songs.
  private func act(_ tap: TransferTap, on records: [DownloadRecord]) {
    Task {
      for record in records {
        await TransferRow.act(tap, on: record, downloads: downloads)
      }
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
  /// Plays the group's shown songs from one of them; nil where songs can't
  /// play here.
  var play: (([Track], Int) -> Void)?
  let release: @MainActor ([String], DownloadOrigin) async -> Void
  /// A record's id is its song's id.
  private func key(_ group: DeviceGroup, songId: String? = nil) -> String {
    "device-remove:" + device + ":" + group.id + (songId.map { "/" + $0 } ?? "")
  }
  private func key(_ group: DeviceGroup, _ record: DownloadRecord) -> String {
    key(group, songId: record.id)
  }
  var body: some View {
    let shown = groups.filter { !model.undo.hides(key($0)) }
    if !shown.isEmpty {
      Section(title) {
        ForEach(shown) { group in
          DisclosureGroup {
            let records = group.records.filter { !model.undo.hides(key(group, $0)) }
            ForEach(records) { record in
              songRow(group, record)
                .swipeActions {
                  Button("Remove", systemImage: "minus.circle", role: .destructive) {
                    remove(group, records: [record])
                  }
                }
            }
          } label: {
            CollectionRow(group.title, subtitle: group.caption, subtitleFigures: true) {
              ArtworkThumb(model: model, source: group.artwork, title: group.title)
            }
          }
          .swipeActions {
            Button("Remove", systemImage: "minus.circle", role: .destructive) {
              remove(group, records: group.records)
            }
          }
        }
      }
    }
  }
  /// The one song row (D-118), with its menu; Apple Watch's songs don't play here.
  private func songRow(_ group: DeviceGroup, _ record: DownloadRecord) -> some View {
    SongRow(
      model: model, track: record.track, play: play == nil ? nil : { playFrom(group, record) })
  }
  private func playFrom(_ group: DeviceGroup, _ record: DownloadRecord) {
    // By id, from the songs shown: one hidden for its Undo never shifts it.
    guard let play,
      let picked = PlaySelection(
        group.records.map(\.track), hiding: { model.undo.hides(key(group, songId: $0.id)) },
        tapped: record.id)
    else { return }
    play(picked.tracks, picked.index)
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

/// One transfer, shown by its mark, which is its control (D-109): tap the
/// clock or progress ring to pause the song, the pause circle to resume it,
/// the retry mark to retry it. Swipe or touch and hold to pause or resume the
/// song, or cancel it (#127).
private struct TransferRow: View {
  let model: AppModel
  @Bindable var downloads: DownloadController
  let record: DownloadRecord
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    HStack(spacing: 4) {
      VStack(alignment: .leading, spacing: 2) {
        Text(record.track.title).lineLimit(1)
        Text(record.track.artist).font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
        // Said in words as well as by the red mark; the mark retries.
        if record.state == "failed", !record.isPaused { StatusText("Couldn't download", kind: .error) }
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .accessibilityElement(children: .combine)
      .accessibilityValue(stateLabel)
      TransferMarkButton(
        TransferControl.phone(record, progress: downloads.progress[record.id]), on: .phone
      ) {
        tap in
        Task { await Self.act(tap, on: record, downloads: downloads) }
      }
    }
    .swipeActions {
      if record.state != "ready" {
        Button("Cancel", systemImage: "xmark", role: .destructive) { cancel() }
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
      Button("Resume", systemImage: "arrow.down.circle") {
        Task { await downloads.resume(id: record.id) }
      }
    } else if ["queued", "downloading"].contains(record.state) {
      Button("Pause", systemImage: "pause.circle") {
        Task { await downloads.pause(id: record.id) }
      }
    }
  }
  /// What a song's mark (or its album's) does to it.
  static func act(_ tap: TransferTap, on record: DownloadRecord, downloads: DownloadController)
    async
  {
    switch tap {
    case .pause: await downloads.pause(id: record.id)
    case .resume: await downloads.resume(id: record.id)
    case .retry:
      await downloads.clearPause(ids: [record.id])
      try? await downloads.enqueue(
        [record.track], quality: record.quality, origin: record.effectiveOrigins.last ?? .direct)
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

/// Downloads grouped the way they were added: by album, or by playlist
/// (`DownloadGrouping`). A song added both ways appears in both groups.
struct DeviceGroup: Identifiable {
  let id: String
  let title: String
  let caption: String
  let origin: DownloadOrigin
  let artwork: AppModel.ArtworkSource?
  let records: [DownloadRecord]
  static func build(_ ready: [DownloadRecord]) -> [DeviceGroup] {
    DownloadGrouping.build(ready).map { group in
      DeviceGroup(
        id: group.id, title: group.title, caption: caption(group.lead, group.records),
        origin: group.origin, artwork: group.artwork, records: group.records)
    }
  }
  private static func caption(_ lead: String, _ records: [DownloadRecord]) -> String {
    let size = ByteCountFormatter.string(
      fromByteCount: records.reduce(0) { $0 + $1.total }, countStyle: .file)
    return "\(lead) · \(records.count) song\(records.count == 1 ? "" : "s") · \(size)"
  }
}

extension DownloadGrouping {
  var artwork: AppModel.ArtworkSource? { records.lazy.compactMap(\.track.artworkSource).first }
  /// For VoiceOver on the group's mark: "Pause album", "Resume playlist".
  var noun: String { origin.playlistId == nil ? "album" : "playlist" }
}

/// The mark on the right of a transfer, which is also its control (D-109):
/// clock, progress ring, pause circle, retry mark or check. A mark that does
/// something is a 44 pt button with its own VoiceOver label and a light tap;
/// it never also opens or taps its row. One that does nothing is only shown.
struct TransferMarkButton: View {
  let mark: TransferMark
  /// Which Downloads page: a finished song is a filled download arrow on
  /// This iPhone and a tick elsewhere (D-116).
  let place: DownloadPlace
  let tap: TransferTap?
  let label: String?
  let action: () -> Void
  @Environment(\.colorScheme) private var scheme
  @State private var taps = 0
  var body: some View {
    if tap != nil, let label {
      Button {
        taps += 1
        action()
      } label: {
        image.frame(width: 44, height: 44).contentShape(Rectangle())
      }
      .buttonStyle(.borderless)
      .accessibilityLabel(label)
      .sensoryFeedback(.impact(weight: .light), trigger: taps)
    } else {
      image.frame(width: 44, height: 44).accessibilityHidden(true)
    }
  }
  @ViewBuilder private var image: some View {
    if case .downloading(let share) = mark {
      ProgressView(value: share).progressViewStyle(.circular)
        .controlSize(.small).tint(PigBrand.accent(scheme))
    } else if let symbol = mark.symbol(on: place) {
      Image(systemName: symbol).foregroundStyle(colour)
    }
  }
  private var colour: Color {
    switch mark {
    case .failed: PigBrand.danger(scheme)
    case .done: PigBrand.accent(scheme)
    default: .secondary
    }
  }
}
extension TransferMarkButton {
  init(
    _ control: TransferControl, on place: DownloadPlace,
    action: @escaping (TransferTap) -> Void
  ) {
    self.init(mark: control.mark, place: place, tap: control.tap, label: control.label) {
      if let tap = control.tap { action(tap) }
    }
  }
}

/// An album's (or playlist's) row on the three Downloads pages (D-109): cover,
/// title, "artist · summary", a progress bar while it is under way, and the
/// album's mark, which acts on all its songs (D-110).
struct TransferGroupLabel: View {
  let model: AppModel
  let title: String
  let subtitle: String
  let artwork: AppModel.ArtworkSource?
  let control: TransferGroupControl
  let place: DownloadPlace
  /// "album", "playlist" or "song", for VoiceOver.
  let noun: String
  /// The server's own measure of the group, where it has one.
  var progress: Double?
  /// Only the mark: the row still opens.
  var disabled = false
  let action: (TransferTap, [String]) -> Void
  var body: some View {
    HStack(spacing: 4) {
      VStack(alignment: .leading, spacing: 4) {
        CollectionRow(title, subtitle: subtitle, subtitleFigures: true) {
          ArtworkThumb(model: model, source: artwork, title: title)
        }
        if control.mark != .done {
          ProgressView(value: min(1, max(0, progress ?? control.progress)))
        }
      }
      .accessibilityElement(children: .combine)
      TransferMarkButton(
        mark: control.mark, place: place, tap: control.tap, label: control.label(noun)
      ) {
        if let tap = control.tap { action(tap, control.ids) }
      }
      .disabled(disabled)
    }
  }
}

/// The Downloads tab (D-120): each place is a card that says how many songs
/// it holds and what is moving now (#209), iPhone and Apple Watch with a
/// storage gauge; the songs on their way to this iPhone or Apple Watch are
/// listed below. Each page keeps Transfers, On your iPhone or Apple Watch,
/// and Settings (#209).
struct DownloadsHubView: View {
  let model: AppModel
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 12) {
        card(
          "iPhone", symbol: "iphone",
          summary: DownloadPlace.phone.hubSummary(songs: phoneSongs, active: model.phoneActiveDownloads),
          gauge: phoneGauge
        ) { DownloadsView(model: model) }
        if model.watch?.supported == true {
          card(
            "Apple Watch", symbol: "applewatch",
            summary: DownloadPlace.watch.hubSummary(
              songs: watchSongs, active: model.watchActiveDownloads),
            gauge: watchGauge
          ) { WatchDownloadsView(model: model) }
        }
        card(
          "Server", symbol: "server.rack",
          summary: DownloadPlace.server.hubSummary(
            songs: model.songCount, active: model.serverActiveRequests),
          gauge: nil
        ) { RequestsView(model: model) }
        InProgressList(model: model)
      }
      .padding(.horizontal, 16).padding(.bottom, 24)
    }
    .navigationTitle("Downloads")
    .refreshable { await model.refreshServerRequests() }
    .task {
      // Server requests have no live progress feed; poll while any are active.
      await model.refreshServerRequests()
      while model.serverActiveRequests > 0, !Task.isCancelled {
        try? await Task.sleep(for: .seconds(10))
        await model.refreshServerRequests()
      }
    }
  }
  private func card<Destination: View>(
    _ title: String, symbol: String, summary: String, gauge: StorageGauge?,
    @ViewBuilder destination: @escaping () -> Destination
  ) -> some View {
    NavigationLink(destination: destination) {
      DownloadPlaceCard(title: title, symbol: symbol, summary: summary, gauge: gauge)
    }
    .buttonStyle(.plain)
  }
  private var phoneSongs: Int { model.downloads?.records.count { $0.state == "ready" } ?? 0 }
  private var watchSongs: Int { model.watch?.receipt?.ready.count ?? 0 }
  private var phoneGauge: StorageGauge? {
    guard let downloads = model.downloads else { return nil }
    let used = downloads.records.filter { $0.state == "ready" }.reduce(Int64(0)) { $0 + $1.total }
    return StorageGauge(used: used, limit: downloads.budgetBytes)
  }
  private var watchGauge: StorageGauge? {
    guard let receipt = model.watch?.receipt else { return nil }
    return StorageGauge(used: receipt.usedBytes, limit: receipt.budgetBytes)
  }
}

/// One place on the Downloads tab: its icon, name and counts, and for a
/// device how full its Storage limit is.
private struct DownloadPlaceCard: View {
  let title: String
  let symbol: String
  let summary: String
  let gauge: StorageGauge?
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(spacing: 12) {
        Image(systemName: symbol).font(.title2).foregroundStyle(PigBrand.accent(scheme))
          .frame(width: 32).accessibilityHidden(true)
        VStack(alignment: .leading, spacing: 2) {
          Text(title).font(.headline).foregroundStyle(PigBrand.text(scheme))
          Text(summary).font(PigType.footnote).foregroundStyle(.secondary).pigFigures()
        }
        Spacer(minLength: 0)
        Image(systemName: "chevron.right").font(.footnote.weight(.semibold))
          .foregroundStyle(.tertiary).accessibilityHidden(true)
      }
      if let gauge {
        ProgressView(value: gauge.fraction).tint(PigBrand.accent(scheme))
          .accessibilityHidden(true)
        Text(gauge.text).font(PigType.footnote).foregroundStyle(.secondary).pigFigures()
      }
    }
    .padding(.horizontal, 16).padding(.vertical, 14)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(PigBrand.surface(scheme), in: .rect(cornerRadius: 14, style: .continuous))
    .contentShape(.rect(cornerRadius: 14))
    .accessibilityElement(children: .combine)
  }
}

/// The songs on their way to this iPhone and Apple Watch, each with its
/// mark; the device's own page acts on them.
private struct InProgressList: View {
  let model: AppModel
  @Environment(\.colorScheme) private var scheme
  private struct Item: Identifiable {
    let id: String
    let record: DownloadRecord
    let control: TransferControl
    let place: DownloadPlace
  }
  private var items: [Item] {
    var items: [Item] = []
    if let downloads = model.downloads {
      for record in downloads.records
      where ["queued", "downloading"].contains(record.state)
        && !model.undo.hides(AppModel.undoKey(download: record.id))
      {
        items.append(
          Item(
            id: "phone:" + record.id, record: record,
            control: TransferControl.phone(record, progress: downloads.progress[record.id]),
            place: .phone))
      }
    }
    if let watch = model.watch, let downloads = watch.downloads {
      let receipt = watch.receipt
      for record in downloads.records
      where record.state != "failed" && !record.delivered
        && (record.checksum == nil || receipt?.ready[record.id] != record.checksum)
        && !model.undo.hides(AppModel.undoKey(watchTransfer: record.id))
      {
        items.append(
          Item(
            id: "watch:" + record.id, record: record,
            control: TransferControl.watch(
              record, onWatch: false, progress: downloads.progress[record.id]),
            place: .watch))
      }
    }
    return Array(items.prefix(30))
  }
  var body: some View {
    let items = items
    if !items.isEmpty {
      Text("In progress").font(PigType.sectionTitle).padding(.top, 12)
        .accessibilityAddTraits(.isHeader)
      VStack(spacing: 0) {
        ForEach(items) { item in
          HStack(spacing: 4) {
            CollectionRow(
              item.record.track.title,
              subtitle: [item.record.track.artist, item.record.track.album ?? ""]
                .filter { !$0.isEmpty }.joined(separator: " · "),
              subtitleLines: 1
            ) {
              ArtworkThumb(
                model: model, source: item.record.track.artworkSource,
                title: item.record.track.title)
            }
            .accessibilityElement(children: .combine)
            TransferMarkButton(mark: item.control.mark, place: item.place, tap: nil, label: nil) {}
          }
          .padding(.vertical, 8)
          if item.id != items.last?.id { RowSeparator() }
        }
      }
      .padding(.leading, 12).padding(.trailing, 4)
      .background(PigBrand.surface(scheme), in: .rect(cornerRadius: 14, style: .continuous))
    }
  }
}
