import PigBrand
import PigDomain
import SwiftUI

struct PendingEditsView: View {
  let model: AppModel
  @State private var loading = false
  /// The change a Discard asks about.
  @State private var discarding: String?
  var body: some View {
    List {
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
          // Asks first: a discarded change can't be brought back (D-79, #183).
          Button("Discard this change", systemImage: "xmark", role: .destructive) {
            discarding = pending.id
          }.disabled(loading)
        }
      }
    }.pigCanvas().navigationTitle("Changes not sent yet").navigationBarTitleDisplayMode(.inline)
      .confirmationDialog(
        "Discard this change? It won't be sent.",
        isPresented: Binding(get: { discarding != nil }, set: { if !$0 { discarding = nil } }),
        titleVisibility: .visible
      ) {
        if let id = discarding {
          Button("Discard this change", role: .destructive) {
            Task { await model.discardEdit(id) }
            discarding = nil
          }
        }
      }
  }
}
