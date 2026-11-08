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
      Section("Releases") {
        Toggle("Albums", isOn: $albums)
        Toggle("Singles", isOn: $singles)
        Toggle("EPs", isOn: $eps)
        Toggle("Monitor new releases", isOn: $monitor)
        Text(
          "Monitoring starts with the current catalogue as a baseline. Request the catalogue separately to acquire existing releases."
        ).font(PigType.footnote)
      }
      if let state {
        Section("Server request") {
          Text(RequestStatus.label(state.status))
          Text(
            "\(state.done.count) of \(state.total) releases reviewed · \(state.created) requests created"
          )
          if let error = state.error { StatusText(error, kind: .error) }
        }
      }
      Section {
        Button("Save monitoring settings") { Task { await save(request: false) } }
        Button("Request selected catalogue") { confirm = true }
          .confirmationDialog("Request all selected releases?", isPresented: $confirm) {
            Button("Request catalogue") { Task { await save(request: true) } }
          } message: {
            Text(
              "This can create many server downloads. Files already available in the household are shared with your library."
            )
          }
        Button("Cancel artist request and monitoring", role: .destructive) {
          Task { await cancel() }
        }
        Button("Refresh status") { Task { await load() } }
      }.disabled(loading || !model.canMutate || !(albums || singles || eps))
      if loading { ProgressView() }
      if let message { StatusText(message) }
    }.pigCanvas().navigationTitle("Artist requests").navigationBarTitleDisplayMode(.inline).task { await load() }
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
  private func cancel() async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    do {
      let value = try await model.performPersonal({ try await $0.cancelArtist(id: artistId) })
      apply(value)
    } catch { message = .error(error.userMessage) }
  }
}
