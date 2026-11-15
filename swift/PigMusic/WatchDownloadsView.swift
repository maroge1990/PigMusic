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
          // The connection row stays here until #208 changes it.
          Text(connection(watch))
          if watch.link?.error != nil
            || records.contains(where: { $0.state == "failed" && !onWatch($0, watch: watch) })
          {
            Button("Retry") { Task { await watch.retry() } }
          }
          if let error = watch.message ?? watch.link?.error {
            StatusText(error, kind: .error)
          }
          Button("Remove all", role: .destructive) { clearing = true }
            .disabled(records.isEmpty)
        }
      }
    }
    .pigCanvas().navigationTitle("Apple Watch").navigationBarTitleDisplayMode(.inline)
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
    default: "Getting ready"
    }
  }
}
