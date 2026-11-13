import PigBrand
import PigDomain
import SwiftUI

struct PendingEditsView: View {
  let model: AppModel
  @State private var loading = false
  var body: some View {
    List {
      Text(
        "Changes are kept on this iPhone and sent in order. If a playlist changed meanwhile, sending stops until you review or discard that change."
      ).font(PigType.footnote)
      Text("Discarding stops retries; it does not undo changes already saved on the server.").font(
        .footnote)
      Button("Send changes now") {
        Task {
          loading = true
          _ = await model.flushEdits()
          await model.refresh()
          loading = false
        }
      }.disabled(loading)
      if model.pendingEdits.isEmpty { Text("All changes sent") }
      ForEach(model.pendingEdits) { pending in
        VStack(alignment: .leading) {
          Text(pending.mutation.action.capitalized)
          if let name = pending.mutation.name { Text(name) }
          if let track = pending.mutation.track {
            Text("\(track.title) · \(track.artist)").font(PigType.footnote)
              .foregroundStyle(.secondary)
          }
          Text(pending.state).font(PigType.footnote).foregroundStyle(.secondary)
          if let message = pending.message { StatusText(message, kind: .error) }
          if let id = pending.mutation.playlistId,
            let playlist = model.playlists.first(where: { $0.id == id })
          {
            NavigationLink("Review current playlist") {
              PlaylistView(model: model, playlist: playlist)
            }
          }
          Button("Discard this change", role: .destructive) {
            Task { await model.discardEdit(pending.id) }
          }.disabled(loading)
        }
      }
    }.pigCanvas().navigationTitle("Changes not sent yet").navigationBarTitleDisplayMode(.inline)
  }
}
