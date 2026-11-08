import PigDomain
import SwiftUI

struct PendingEditsView: View {
  let model: AppModel
  @State private var loading = false
  var body: some View {
    List {
      Text(
        "Edits are saved on this phone and replayed in order. A changed playlist stops replay until you review or discard that edit."
      ).font(.footnote)
      Text("Discarding stops retries; it does not undo changes already saved on the server.").font(
        .footnote)
      Button("Sync edits now") {
        Task {
          loading = true
          _ = await model.flushEdits()
          await model.refresh()
          loading = false
        }
      }.disabled(loading)
      if model.pendingEdits.isEmpty { Text("All edits synced") }
      ForEach(model.pendingEdits) { pending in
        VStack(alignment: .leading) {
          Text(pending.mutation.action.capitalized)
          if let name = pending.mutation.name { Text(name) }
          if let track = pending.mutation.track {
            Text("\(track.title) · \(track.artist)").font(.caption)
          }
          Text(pending.state).font(.caption)
          if let message = pending.message { StatusText(message, kind: .error) }
          if let id = pending.mutation.playlistId,
            let playlist = model.playlists.first(where: { $0.id == id })
          {
            NavigationLink("Review current playlist") {
              PlaylistView(model: model, playlist: playlist)
            }
          }
          Button("Discard this pending edit", role: .destructive) {
            Task { await model.discardEdit(pending.id) }
          }.disabled(loading)
        }
      }
    }.pigCanvas().navigationTitle("Pending edits")
  }
}
