import PigBrand
import PigCompanion
import PigDomain
import PigDownloads
import SwiftUI

/// Music sent to Apple Watch: transfers first, then what is on the Watch grouped
/// by album and playlist. Music is chosen from album, artist, playlist and song pages.
struct WatchDownloadsView: View {
  @Environment(\.colorScheme) private var scheme
  let model: AppModel
  @State private var removing: DeviceGroup?
  var body: some View {
    List {
      if let watch = model.watch {
        let records = watch.downloads?.records ?? []
        let delivered = records.filter { onWatch($0, watch: watch) }
        let transfers = records.filter { !onWatch($0, watch: watch) || $0.cleared != true }
        if records.isEmpty {
          Text("Nothing sent yet. Use Send to Apple Watch on an album, artist, playlist or song.")
            .foregroundStyle(.secondary)
        } else {
          Section { readiness(records: records, delivered: delivered, watch: watch) }
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
                    .font(PigType.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 0)
                mark(record, watch: watch).frame(width: 28, height: 28)
              }
              .accessibilityElement(children: .combine)
              .swipeActions {
                if !onWatch(record, watch: watch) {
                  Button("Cancel", role: .destructive) { Task { await watch.remove(record.id) } }
                }
              }
            }
          }
        }
        let groups = DeviceGroup.build(delivered)
        if !groups.isEmpty {
          Section("On Apple Watch") {
            ForEach(groups) { group in
              DisclosureGroup {
                ForEach(group.records) { record in
                  HStack {
                    Text(record.track.title).lineLimit(1)
                    Spacer(minLength: 0)
                    RemoveButton(label: "Remove \(record.track.title) from Apple Watch") {
                      Task { await watch.release([record.id], origin: group.origin) }
                    }
                  }
                }
              } label: {
                HStack(spacing: 12) {
                  ArtworkThumb(model: model, source: group.artwork, title: group.title, size: 44)
                  VStack(alignment: .leading, spacing: 2) {
                    Text(group.title).lineLimit(1)
                    Text(group.caption).font(PigType.caption).foregroundStyle(.secondary)
                      .lineLimit(1)
                  }
                  Spacer(minLength: 0)
                  RemoveButton(label: "Remove \(group.title) from Apple Watch") { removing = group }
                }
              }
            }
          }
        }
        Section("Status") {
          Text(connection(watch))
          if let receipt = watch.receipt {
            Text(
              "\(receipt.ready.count) songs on Watch · \(ByteCountFormatter.string(fromByteCount: receipt.usedBytes, countStyle: .file))"
            )
          }
          Button("Retry Watch transfers") { Task { await watch.retry() } }
          if let error = watch.message ?? watch.link?.error {
            StatusText(error, kind: .error)
          }
          Text(
            "Playlists sent to the Watch update when they change. Copies keep downloading to this iPhone while it is locked; the system decides when to pass them to the Watch. AAC copies also use storage on this iPhone."
          ).font(PigType.footnote)
        }
      }
    }
    .pigCanvas().navigationTitle("Apple Watch")
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
    .confirmationDialog(
      removing.map { "Remove \($0.title) from Apple Watch?" } ?? "",
      isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
      titleVisibility: .visible
    ) {
      if let group = removing, let watch = model.watch {
        Button("Remove", role: .destructive) {
          Task { await watch.release(group.records.map(\.id), origin: group.origin) }
        }
      }
    } message: {
      Text("Songs also in another album or playlist on the Watch stay there.")
    }
  }
  /// Only songs the Watch confirmed count as ready; queued or prepared on the
  /// phone is never presented as on the Watch.
  @ViewBuilder private func readiness(
    records: [DownloadRecord], delivered: [DownloadRecord], watch: WatchPhoneController
  ) -> some View {
    let failed = records.filter { $0.state == "failed" && !onWatch($0, watch: watch) }.count
    let summary = WatchReadinessSummary(
      ready: delivered.count, waiting: records.count - delivered.count - failed, failed: failed,
      seconds: delivered.reduce(0) { $0 + ($1.track.duration ?? 0) },
      bytes: delivered.reduce(Int64(0)) { $0 + $1.total })
    let waiting = summary.waiting
    VStack(alignment: .leading, spacing: 6) {
      Label(
        summary.complete ? "Ready for your run" : "Almost ready",
        systemImage: summary.complete ? "checkmark.circle.fill" : "hourglass"
      ).font(.headline)
      Text(
        "\(delivered.count) song\(delivered.count == 1 ? "" : "s") on Watch · \(summary.listening) · \(ByteCountFormatter.string(fromByteCount: summary.bytes, countStyle: .file))"
      ).font(PigType.subhead)
      if waiting > 0 {
        Text("\(waiting) still on the way. Keep your iPhone near the Watch.")
          .font(PigType.footnote).foregroundStyle(.secondary)
      }
      if failed > 0 {
        Text("\(failed) failed. Tap the retry mark next to each song.")
          .font(PigType.footnote).foregroundStyle(PigBrand.danger(scheme))
      }
      if let confirmed = watch.confirmedAt {
        Text("Watch confirmed \(confirmed.formatted(.relative(presentation: .named)))")
          .font(PigType.footnote).foregroundStyle(.secondary)
      }
    }
    .accessibilityElement(children: .combine)
  }
  private func connection(_ watch: WatchPhoneController) -> String {
    let link = watch.link
    return WatchConnectionStatus(
      supported: watch.supported && link != nil, activated: link?.activated == true,
      error: link?.error, paired: link?.paired == true, installed: link?.installed == true,
      reachable: link?.reachable == true
    ).text
  }
  private func onWatch(_ record: DownloadRecord, watch: WatchPhoneController) -> Bool {
    record.checksum != nil && watch.receipt?.ready[record.id] == record.checksum
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
    default: "Preparing on server"
    }
  }
}
