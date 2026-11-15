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
  @Environment(\.colorScheme) private var scheme
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
            ForEach(transfers) { record in
              HStack(spacing: 12) {
                ArtworkThumb(
                  model: model, source: record.track.artworkSource, title: record.track.title)
                VStack(alignment: .leading, spacing: 2) {
                  Text(record.track.title).lineLimit(1)
                  Text("\(status(record, watch: watch)) · \(record.track.artist)")
                    .font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 0)
                mark(record, watch: watch).frame(width: 28, height: 28)
              }
              .accessibilityElement(children: .combine)
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
    .confirmationDialog("Remove all music from Apple Watch?", isPresented: $clearing) {
      if let watch = model.watch {
        Button("Remove all", role: .destructive) { Task { await watch.removeAll() } }
      }
    }
  }
  private func attention(_ watch: WatchPhoneController, records: [DownloadRecord])
    -> WatchAttention?
  {
    let waiting = records.filter { $0.state == "ready" && !onWatch($0, watch: watch) }.map(\.id)
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
  @ViewBuilder private func mark(_ record: DownloadRecord, watch: WatchPhoneController)
    -> some View
  {
    if onWatch(record, watch: watch) {
      Image(systemName: "checkmark.circle.fill").foregroundStyle(PigBrand.accent(scheme))
    } else if record.state == "failed" {
      Button {
        Task { await watch.enqueue([record.track], origin: record.effectiveOrigins.last ?? .direct) }
      } label: {
        Image(systemName: "exclamationmark.arrow.circlepath").foregroundStyle(PigBrand.danger(scheme))
      }.buttonStyle(.borderless).accessibilityLabel("Retry")
    } else if record.state == "ready" {
      Image(systemName: "applewatch.radiowaves.left.and.right").foregroundStyle(.secondary)
    } else {
      ProgressView().controlSize(.small)
    }
  }
  private func status(_ record: DownloadRecord, watch: WatchPhoneController) -> String {
    if onWatch(record, watch: watch) { return "On Watch" }
    return switch record.state {
    case "ready": "Waiting for Watch"
    case "failed": "Failed"
    case "downloading": "Downloading to iPhone"
    default: "Getting ready"
    }
  }
}
