import PigBrand
import PigCompanion
import PigDomain
import PigDownloads
import SwiftUI

/// Music sent to Apple Watch: transfers, then what is on the Watch grouped by
/// album and playlist, then its settings: the same sections as the iPhone
/// and Server pages, with no explanatory text (#209, D-101). Music is chosen
/// from album, artist, playlist and song pages.
struct WatchDownloadsView: View {
  let model: AppModel
  @State private var clearing = false
  @State private var now = Date()
  /// When the Watch app was first reported missing after activation settled.
  @State private var missingSince: Date?
  var body: some View {
    List {
      if let watch = model.watch {
        let records = watch.downloads?.records ?? []
        let delivered = records.filter { onWatch($0, watch: watch) }
        let transfers = records.filter {
          (!onWatch($0, watch: watch) || $0.cleared != true)
            && !model.undo.hides(AppModel.undoKey(watchTransfer: $0.id))
        }
        if records.isEmpty {
          Text("Nothing sent yet").foregroundStyle(.secondary)
        }
        if !transfers.isEmpty {
          Section("Transfers") {
            // By album or playlist, as on the Server page (D-109).
            ForEach(DownloadGrouping.transfers(transfers) { onWatch($0, watch: watch) }) { group in
              transferGroup(group, watch: watch)
            }
          }
        }
        DeviceGroupsSection(
          model: model, title: "On your watch", device: "Apple Watch",
          groups: DeviceGroup.build(delivered)
        ) { ids, origin in await watch.release(ids, origin: origin) }
        Section("Settings") {
          // Only when Mark must act (D-105).
          if let attention = attention(watch, records: records) {
            Text(attention.text)
          }
          if watch.link?.error != nil
            || records.contains(where: { $0.state == "failed" && !onWatch($0, watch: watch) })
          {
            Button("Retry") { Task { await watch.retry() } }
          }
          if let error = watch.message {
            StatusText(error, kind: .error)
          }
          Button("Remove all", role: .destructive) { clearing = true }
            .disabled(records.isEmpty)
        }
      }
    }
    .pigCanvas().navigationTitle("Apple Watch").navigationBarTitleDisplayMode(.inline)
    .task { await watchClock() }
    .toolbar {
      if let watch = model.watch {
        let records = watch.downloads?.records ?? []
        Button("Clear finished") {
          Task {
            await watch.downloads?.clearFinished(
              ids: Set(records.filter { onWatch($0, watch: watch) }.map(\.id)))
          }
        }.disabled(!records.contains { onWatch($0, watch: watch) && $0.cleared != true })
      }
    }
    // A centred alert, not Undo: it removes every song (D-114).
    .alert("Remove all songs from Apple Watch?", isPresented: $clearing) {
      Button("Cancel", role: .cancel) {}
      if let watch = model.watch {
        Button("Remove all", role: .destructive) { Task { await watch.removeAll() } }
      }
    } message: {
      Text("This can't be undone.")
    }
  }
  private func attention(_ watch: WatchPhoneController, records: [DownloadRecord])
    -> WatchAttention?
  {
    // A song the person paused isn't waiting for the Watch (D-109).
    let waiting = records.filter {
      $0.state == "ready" && !$0.isHeld && !onWatch($0, watch: watch)
    }.map(\.id)
    return WatchAttention(
      activated: watch.link?.activated == true, error: watch.link?.error,
      missingSince: missingSince,
      oldestWait: WatchAttention.oldestWait(waiting, log: watch.deliveryLog), now: now)
  }
  /// Re-reads the Watch's state every few seconds, so the 2- and 30-minute
  /// thresholds take effect while the page stays open.
  private func watchClock() async {
    while !Task.isCancelled {
      let link = model.watch?.link
      now = Date()
      missingSince = WatchAttention.missing(
        since: missingSince, activated: link?.activated == true, paired: link?.paired == true,
        installed: link?.installed == true, now: now)
      do { try await Task.sleep(for: .seconds(5)) } catch { return }
    }
  }
  private func onWatch(_ record: DownloadRecord, watch: WatchPhoneController) -> Bool {
    // A song whose iPhone copy went once the Watch confirmed it (#218).
    record.delivered
      || (record.checksum != nil && watch.receipt?.ready[record.id] == record.checksum)
  }
  private func control(_ record: DownloadRecord, watch: WatchPhoneController) -> TransferControl {
    TransferControl.watch(
      record, onWatch: onWatch(record, watch: watch),
      progress: watch.downloads?.progress[record.id])
  }
  /// An album's row, whose mark acts on all its songs (D-110), opening to
  /// its songs.
  private func transferGroup(_ group: DownloadGrouping, watch: WatchPhoneController)
    -> some View
  {
    DisclosureGroup {
      ForEach(group.records) { record in
        transferRow(record, watch: watch)
      }
    } label: {
      let album = TransferGroupControl(group.records.map { ($0.id, control($0, watch: watch)) })
      TransferGroupLabel(
        model: model, title: group.title,
        subtitle: group.lead + " · " + album.summary(done: "on your watch"),
        artwork: group.artwork, control: album, noun: group.noun
      ) { tap, ids in
        let records = group.records.filter { ids.contains($0.id) }
        Task { await act(tap, on: records, watch: watch) }
      }
    }
  }
  /// A song, whose mark is its control (D-109). Any song not yet on the Watch
  /// pauses, including one waiting for the Watch, whose audio is held back.
  private func transferRow(_ record: DownloadRecord, watch: WatchPhoneController) -> some View {
    HStack(spacing: 4) {
      VStack(alignment: .leading, spacing: 2) {
        Text(record.track.title).lineLimit(1)
        Text("\(status(record, watch: watch)) · \(record.track.artist)")
          .font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .accessibilityElement(children: .combine)
      TransferMarkButton(control(record, watch: watch)) { tap in
        Task { await act(tap, on: [record], watch: watch) }
      }
    }
    .swipeActions {
      if !onWatch(record, watch: watch) {
        // At once, with Undo (D-79), like the iPhone's own downloads.
        Button("Cancel", role: .destructive) {
          let id = record.id
          model.undo.schedule(
            AppModel.undoKey(watchTransfer: id), label: "Cancelled " + record.track.title
          ) {
            await watch.remove(id)
            return true
          }
        }
      }
    }
  }
  private func act(_ tap: TransferTap, on records: [DownloadRecord], watch: WatchPhoneController)
    async
  {
    switch tap {
    case .pause: await watch.pause(records.map(\.id))
    case .resume: await watch.resume(records.map(\.id))
    case .retry:
      for record in records {
        await watch.enqueue([record.track], origin: record.effectiveOrigins.last ?? .direct)
      }
    }
  }
  private func status(_ record: DownloadRecord, watch: WatchPhoneController) -> String {
    if onWatch(record, watch: watch) { return "On Watch" }
    if record.isPaused || record.isHeld { return "Paused" }
    return switch record.state {
    case "ready": "Waiting for Watch"
    case "failed": "Failed"
    case "downloading": "Downloading to iPhone"
    default: "Getting ready"
    }
  }
}
