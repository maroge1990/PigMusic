import PigAPI
import PigBrand
import PigDomain
import SwiftUI
import UniformTypeIdentifiers

struct PlaylistToolsView: View {
  let model: AppModel
  @State private var name = ""
  @State private var selecting = false
  @State private var imported: Data?
  @State private var preview: ImportPreview?
  @State private var importSelection: AppleImportSelection?
  @State private var selectedPlaylists: Set<Int> = []
  @State private var includeOtherTracks = false
  @State private var message: StatusMessage?
  @State private var loading = false
  var body: some View {
    Form {
      Section("New playlist") {
        TextField("Playlist name", text: $name)
        Button("Create playlist") { Task { await create() } }.disabled(
          name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || loading
            || !model.canMutate)
      }
      Section("Apple Music XML") {
        Text("Import personal playlists and matching metadata. This does not download songs.").font(
          PigType.footnote)
        Button("Choose XML export") { selecting = true }.disabled(loading)
        if let imported {
          Text(
            "Selected \(ByteCountFormatter.string(fromByteCount:Int64(imported.count),countStyle:.file)) export"
          )
          if let importSelection {
            ForEach(importSelection.playlists) { playlist in
              Toggle(
                "\(playlist.name) · \(playlist.songs) songs",
                isOn: Binding(
                  get: { selectedPlaylists.contains(playlist.id) },
                  set: { enabled in
                    if enabled {
                      selectedPlaylists.insert(playlist.id)
                    } else {
                      selectedPlaylists.remove(playlist.id)
                    }
                    preview = nil
                  }))
            }
            Toggle("Include music outside selected playlists", isOn: $includeOtherTracks)
              .onChange(of: includeOtherTracks) { preview = nil }
          }
          Button("Review matches") { Task { await review(imported) } }.disabled(
            loading || selectedPlaylists.isEmpty)
          if let preview {
            Text(
              "\(preview.playlists) playlists · \(preview.tracks) songs. \(preview.saved) in your library, \(preview.household) in the household, \(preview.missing) missing."
            ).font(PigType.footnote)
          }
          Button("Import playlists") { Task { await submit(imported) } }.disabled(
            loading || !model.canMutate || selectedPlaylists.isEmpty || preview == nil)
        }
      }
      if loading { ProgressView() }
      if let message { StatusText(message) }
    }.pigCanvas().navigationTitle("Playlist tools").navigationBarTitleDisplayMode(.inline)
      .fileImporter(isPresented: $selecting, allowedContentTypes: [.xml]) { result in
        do {
          let url = try result.get()
          let access = url.startAccessingSecurityScopedResource()
          defer { if access { url.stopAccessingSecurityScopedResource() } }
          let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
          guard size > 0 && size <= 20 * 1024 * 1024 else { throw CocoaError(.fileReadTooLarge) }
          let data = try Data(contentsOf: url)
          let selection = try AppleImportSelection(data: data)
          imported = data
          importSelection = selection
          selectedPlaylists = Set(selection.playlists.map(\.id))
          preview = nil
          message = .info(
            "Review the selected export before importing. Existing imported playlists are updated; unrelated playlists are retained."
          )
        } catch { message = .error(error.userMessage) }
      }
  }
  private func create() async {
    loading = true
    defer { loading = false }
    let value = name.trimmingCharacters(in: .whitespacesAndNewlines)
    do {
      let confirmed = try await model.saveEdit(OfflineMutation(action: "create", name: value))
      name = ""
      message = .info(
        confirmed ? "Playlist created." : "New playlist saved on this iPhone; it will be sent when you're online.")
    } catch { message = .error(error.userMessage) }
  }
  private func review(_ data: Data) async {
    loading = true
    defer { loading = false }
    do {
      let selected =
        try importSelection?.export(
          selected: selectedPlaylists, includeOtherTracks: includeOtherTracks) ?? data
      preview = try await model.readCatalogue { try await $0.previewImport(selected) }
      message = nil
    } catch { message = .error(error.userMessage) }
  }
  private func submit(_ data: Data) async {
    loading = true
    defer { loading = false }
    do {
      let selected =
        try importSelection?.export(
          selected: selectedPlaylists, includeOtherTracks: includeOtherTracks) ?? data
      let result = try await model.performPersonal { try await $0.importApple(selected) }
      imported = nil
      message = .info("Imported \(result.playlists) playlists and \(result.tracks) song records.")
    } catch { message = .error(error.userMessage) }
  }
}

struct AddToPlaylistView: View {
  let model: AppModel
  let track: Track
  @State private var message: StatusMessage?
  @State private var loading = false
  var body: some View {
    List {
      ForEach(model.playlists.filter { $0.favourites != true }) { playlist in
        Button(playlist.name) {
          Task {
            loading = true
            defer { loading = false }
            do {
              let confirmed = try await model.saveEdit(
                OfflineMutation(action: "append", playlistId: playlist.id, track: track))
              message = .info(
                confirmed
                  ? "Added to \(playlist.name). Repeated songs are kept as separate entries."
                  : "Added on this iPhone; it will be sent when you're online.")
            } catch { message = .error(error.userMessage) }
          }
        }.disabled(loading || !model.canMutate)
      }
      NavigationLink("Create or import playlists") { PlaylistToolsView(model: model) }
      if loading { ProgressView() }
      if let message { StatusText(message) }
    }.pigCanvas().navigationTitle("Add to playlist").navigationBarTitleDisplayMode(.inline)
  }
}

struct PlaylistView: View {
  @Environment(\.colorScheme) private var scheme
  @Bindable var model: AppModel
  @State private var playlist: Playlist
  @State private var name: String
  @State private var message: StatusMessage?
  @State private var loading = false
  @State private var deleting = false
  @State private var reloadAfterEdit = false
  @Environment(\.dismiss) private var dismiss
  init(model: AppModel, playlist: Playlist) {
    self.model = model
    _playlist = State(initialValue: playlist)
    _name = State(initialValue: playlist.name)
  }
  private var songs: [Track] { (playlist.entries ?? []).map(\.playbackTrack) }
  /// Songs this library lacks: household files are added, the rest requested
  /// (the server ignores repeat requests).
  private var missing: [PlaylistEntry] { (playlist.entries ?? []).filter { $0.savedTrack == nil } }
  private var locked: Bool {
    loading || !model.canMutate
      || model.pendingEdits.contains(where: { $0.mutation.playlistId == playlist.id })
  }
  var body: some View {
    List {
      Section {
        DetailHeader(
          model: model, source: songs.lazy.compactMap(\.artworkSource).first,
          eyebrow: playlist.favourites == true ? "FAVOURITES" : "PLAYLIST", title: playlist.name,
          subtitle: "\(songs.count) song\(songs.count == 1 ? "" : "s")"
        ).preparesPlayback(songs, model: model)
        HStack(spacing: 10) {
          PlayCapsule { model.play(songs) }.disabled(songs.isEmpty)
          RoundIcon(label: "Shuffle", systemImage: "shuffle") { model.play(songs.shuffled()) }
            .disabled(songs.isEmpty)
          DownloadMenu(
            model: model, tracks: songs,
            origin: DownloadOrigin(playlistId: playlist.id, playlistName: playlist.name),
            missingCount: missing.count
          ) { await addMissing() }
          Spacer()
        }.buttonStyle(.plain)
        if let message { StatusText(message) }
        // Edits saved offline show here, and a rejected one says so where it was made.
        if let failed = model.pendingEdits.first(where: {
          $0.mutation.playlistId == playlist.id && $0.state != "queued"
        }) {
          VStack(alignment: .leading, spacing: 4) {
            Text(failed.message ?? "An edit to this playlist could not be applied.")
              .font(PigType.footnote).foregroundStyle(PigBrand.danger(scheme))
            NavigationLink("Review edit") { PendingEditsView(model: model) }
              .font(PigType.footnote.weight(.semibold))
          }
        } else if model.pendingEdits.contains(where: { $0.mutation.playlistId == playlist.id }) {
          Label("Changes not sent yet", systemImage: "clock.arrow.circlepath")
            .font(PigType.footnote).foregroundStyle(.secondary)
        }
      }.listRowSeparator(.hidden).listRowBackground(Color.clear)
      Section {
        ForEach(Array((playlist.entries ?? []).enumerated()), id: \.element.entryId) {
          index, entry in
          SongRow(model: model, track: entry.playbackTrack) { model.play(songs, at: index) }
            .swipeActions {
              Button("Remove", role: .destructive) { Task { await remove(index) } }.disabled(
                locked)
            }
        }
        .onMove { from, to in
          var order = Array((playlist.entries ?? []).indices)
          order.move(fromOffsets: from, toOffset: to)
          Task { await edit(order: order) }
        }
        // Favourites keep the order they were favourited in; the server has no reorder for them.
        .moveDisabled(locked || playlist.favourites == true)
      }
      if playlist.favourites != true {
        // The album pages' settings block. A List (not their ScrollView) keeps
        // swipe to remove and drag to reorder (#158).
        DetailSettings {
          HStack {
            TextField("Name", text: $name).submitLabel(.done)
              .onSubmit { Task { await edit(name: name) } }
            Button("Rename") { Task { await edit(name: name) } }.buttonStyle(.pigSecondary)
              .disabled(locked || name.isEmpty || name == playlist.name)
          }
          // Anchored to its button, so it opens there rather than at the top.
          Button("Delete playlist", role: .destructive) { deleting = true }.disabled(locked)
            .confirmationDialog("Delete \(playlist.name)?", isPresented: $deleting) {
              Button("Delete playlist", role: .destructive) { Task { await delete() } }
            }
        }.listRowSeparator(.hidden).listRowBackground(Color.clear)
      }
    }.listStyle(.plain).pigCanvas().navigationTitle(playlist.name).navigationBarTitleDisplayMode(.inline)
      .toolbar {
        if playlist.favourites != true { EditButton() }
        Button("Refresh", systemImage: "arrow.clockwise") { Task { await load() } }.disabled(
          loading)
      }
      .task { await load() }
      // A sync changed the library: show the playlist as the server now has it.
      .onChange(of: model.libraryRevision) { Task { await load() } }
      .onChange(of: reloadAfterEdit) { _, reload in
        guard reload else { return }
        reloadAfterEdit = false
        Task { await load() }
      }
  }
  private func load() async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    let id = playlist.id
    do {
      playlist = try await model.cachedCatalogue(
        key: "playlist:" + id, conditional: { try await $0.playlist(id: id, etag: $1) })
      name = playlist.name
      message = nil
    } catch is CancellationError {} catch { message = .error(error.userMessage) }
  }
  private func addMissing() async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    let household = missing.compactMap(\.householdTrack).map(\.id)
    let requested = missing.filter { $0.householdTrack == nil }.map(\.entryTrack)
    do {
      var added = 0
      if !household.isEmpty {
        added = try await model.performPersonal { try await $0.addMembership(trackIds: household) }
      }
      var queued = 0
      for batch in stride(from: 0, to: requested.count, by: 500) {
        let tracks = Array(requested[batch..<min(batch + 500, requested.count)])
        queued += try await model.performPersonal { try await $0.requestMusic(tracks) }.created
      }
      let done = [
        added > 0 ? "Added \(added) song\(added == 1 ? "" : "s") to your library." : nil,
        queued > 0 ? "Requested \(queued) song\(queued == 1 ? "" : "s")." : nil,
      ].compactMap { $0 }.joined(separator: " ")
      message = .info(done.isEmpty ? "These songs are already requested." : done)
      reloadAfterEdit = true
    } catch is CancellationError {} catch { message = .error(error.userMessage) }
  }
  private func edit(name: String? = nil, order: [Int]? = nil) async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    let id = playlist.id
    let revision = playlist.revision
    do {
      let confirmed = try await model.saveEdit(
        OfflineMutation(
          action: order == nil ? "rename" : "reorder", playlistId: id, name: name,
          revision: revision, order: order))
      message =
        confirmed
        ? nil : .info("Change saved on this iPhone. Other playlist changes wait until this one is sent.")
      if confirmed { reloadAfterEdit = true }
    } catch { message = .error(error.userMessage + " Refresh before reapplying your edit.") }
  }
  private func remove(_ index: Int) async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    let id = playlist.id
    let revision = playlist.revision
    // Favourites are removed by unfavouriting that exact song; the server rejects
    // a positional removal, which would block the edits queued behind it.
    let entries = playlist.entries ?? []
    let mutation =
      if playlist.favourites == true, entries.indices.contains(index) {
        OfflineMutation(action: "favourite", track: entries[index].entryTrack, favourite: false)
      } else {
        OfflineMutation(action: "remove", playlistId: id, revision: revision, position: index)
      }
    do {
      let confirmed = try await model.saveEdit(mutation)
      message = confirmed ? nil : .info("Removed on this iPhone; it will be sent when you're online.")
      if confirmed { reloadAfterEdit = true }
    } catch { message = .error(error.userMessage + " Refresh before reapplying your edit.") }
  }
  private func delete() async {
    loading = true
    defer { loading = false }
    let id = playlist.id
    let revision = playlist.revision
    do {
      _ = try await model.saveEdit(
        OfflineMutation(action: "delete", playlistId: id, revision: revision))
      dismiss()
    } catch { message = .error(error.userMessage) }
  }
}
