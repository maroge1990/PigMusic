import PigAPI
import PigBrand
import PigDomain
import SwiftUI

struct ArtistAcquisitionView: View {
  let model: AppModel
  let artistId: String
  @State private var state: ArtistAcquisition?
  @State private var albums = true
  @State private var singles = false
  @State private var eps = false
  @State private var monitor = false
  @State private var loading = false
  @State private var message: StatusMessage?
  @State private var confirm = false
  var body: some View {
    Form {
      Section("Album types") {
        Toggle("Albums", isOn: $albums)
        Toggle("Singles", isOn: $singles)
        Toggle("EPs", isOn: $eps)
        Toggle("Monitor new albums", systemImage: "bell", isOn: $monitor)
        Text(
          "Monitoring starts with the current catalogue as a baseline. Use Add albums to library for the albums already out."
        ).font(PigType.footnote)
      }
      if let state, !cancelling {
        Section("Progress") {
          Text(RequestStatus.label(state.status))
          Text(
            "\(state.done.count) of \(state.total) albums reviewed · \(state.created) song\(state.created == 1 ? "" : "s") being added"
          )
          if let error = state.error { StatusText(error, kind: .error) }
        }
      }
      Section {
        Button("Save monitoring settings") { Task { await save(request: false) } }
        Button("Add selected albums", systemImage: "plus") { confirm = true }
          .confirmationDialog("Add all selected albums to library?", isPresented: $confirm) {
            Button("Add albums to library") { Task { await save(request: true) } }
          } message: {
            Text("This can add many albums at once.")
          }
        Button("Cancel adding and monitoring", systemImage: "xmark", role: .destructive) {
          cancel()
        }
        Button("Refresh", systemImage: "arrow.clockwise") { Task { await load() } }
      }.disabled(loading || cancelling || !model.canMutate || !(albums || singles || eps))
      if loading { ProgressView() }
      if let message { StatusText(message) }
    }.pigCanvas().navigationTitle("Artist monitoring").navigationBarTitleDisplayMode(.inline).task { await load() }
      // Undone: the page shows the settings as the server still has them.
      .onChange(of: cancelling) { _, now in if !now { Task { await load() } } }
  }
  private func apply(_ value: ArtistAcquisition) {
    state = value
    monitor = value.monitor
    albums = value.kinds.contains("album")
    singles = value.kinds.contains("single")
    eps = value.kinds.contains("ep")
  }
  private func load() async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    do {
      let value = try await model.readCatalogue({ try await $0.artistAcquisition(id: artistId) })
      apply(value)
    } catch { message = .error(error.userMessage) }
  }
  private func save(request: Bool) async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    let kinds = [("album", albums), ("single", singles), ("ep", eps)].filter(\.1).map(\.0)
    let monitoring = monitor
    do {
      let value = try await model.performPersonal({
        try await $0.configureArtist(
          id: artistId, kinds: kinds, monitor: monitoring, request: request)
      })
      apply(value)
    } catch { message = .error(error.userMessage) }
  }
  /// At once, with Undo (D-79, #183): the page shows nothing being added or
  /// monitored, and the server hears of it when the Undo runs out.
  private func cancel() {
    model.cancelArtistLater(id: artistId) { value in apply(value) }
  }
  private var cancelling: Bool { model.undo.hides(AppModel.undoKey(artistAdding: artistId)) }
}
