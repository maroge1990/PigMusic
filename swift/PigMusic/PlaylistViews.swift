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
          message = nil
        } catch { message = .error(error.userMessage) }
      }
  }
  private func create() async {
    loading = true
    defer { loading = false }
    // The server's rule, before it is queued (Codex round 2 on batch G).
    guard let value = PlaylistName.accepted(name) else {
      message = .error(PlaylistName.refusal)
      return
    }
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
  /// With each song's position in the playlist, without songs waiting for
  /// their removal's Undo (#183).
  /// A stale copy can still list a song this iPhone removed (Codex); the
  /// page's menu uses the same songs.
  private var shown: [(position: Int, entry: PlaylistEntry)] { model.shownEntries(playlist) }
  private var entries: [PlaylistEntry] { shown.map(\.entry) }
  /// A removal from this playlist is waiting or being sent: the page keeps
  /// the version it was made on until all of them are sent, so a waiting row
  /// can't come back with a reload.
  private var removing: Bool { model.undo.pending(prefix: "playlist-entry:" + playlist.id + "@") }
  private var songs: [Track] { entries.map(\.playbackTrack) }
  /// Songs this library lacks: household files are added, the rest requested
  /// (the server ignores repeat requests).
  private var missing: [PlaylistEntry] { entries.filter { $0.savedTrack == nil } }
  private var locked: Bool {
    loading || !model.canMutate
      || model.pendingEdits.contains(where: { $0.mutation.playlistId == playlist.id })
  }
  /// Read from the server or the library with its songs: an empty one
  /// offers Add songs (D-120).
  private var empty: EmptyPage? {
    EmptyPage.playlist(songs: shown.count, loaded: playlist.entries != nil && !removing)
  }
  var body: some View {
    List {
      Section {
        // The cover, name and figures, then Play and Shuffle (D-120).
        VStack(spacing: 10) {
          CoverMosaicView(
            model: model, tracks: songs, favourites: playlist.favourites == true, width: 512
          )
          .frame(width: 200, height: 200)
          .shadow(color: .black.opacity(0.15), radius: 12, y: 6)
          Text(playlist.name).font(.title2.weight(.bold)).multilineTextAlignment(.center)
            .accessibilityAddTraits(.isHeader)
          if empty == nil {
            Text(CollectionFacts.line(count: songs.count, seconds: entries.reduce(0) { $0 + ($1.duration ?? 0) }))
              .font(PigType.subhead).pigFigures().foregroundStyle(.secondary)
            PlayShuffleRow {
              model.play(songs)
            } shuffle: {
              model.play(songs.shuffled())
            }
            .disabled(songs.isEmpty).padding(.top, 4)
          }
        }
        .frame(maxWidth: .infinity)
        .preparesPlayback(songs, model: model)
        if let empty {
          EmptyPageView(page: empty, systemImage: "music.note") { model.openSearch() }
        }
        if let message { StatusText(message) }
        // Edits saved offline show here, and a rejected one says so where it was made.
        if let failed = model.pendingEdits.first(where: {
          $0.mutation.playlistId == playlist.id && $0.state != "queued"
        }) {
          VStack(alignment: .leading, spacing: 4) {
            Text(failed.message ?? "Couldn't apply an edit to this playlist.")
              .font(PigType.footnote).foregroundStyle(PigBrand.danger(scheme))
            NavigationLink("Review edit") { PendingEditsView(model: model) }
              .font(PigType.footnote.weight(.semibold))
          }
        } else if model.pendingEdits.contains(where: { $0.mutation.playlistId == playlist.id }) {
          Label("Changes not sent yet", systemImage: "clock")
            .font(PigType.footnote).foregroundStyle(.secondary)
        }
      }.listRowSeparator(.hidden).listRowBackground(Color.clear)
      Section {
        ForEach(Array(shown.enumerated()), id: \.element.entry.entryId) { index, row in
          SongRow(
            model: model, track: row.entry.playbackTrack,
            page: locked
              ? []
              : [
                SongPageAction(.removeFromPlaylist) {
                  model.removeLater(at: row.position, from: playlist)
                }
              ]
          ) { model.play(songs, at: index) }
            .swipeActions {
              // At once, with Undo (D-79): sent when the Undo runs out.
              Button("Remove", systemImage: "xmark", role: .destructive) {
                model.removeLater(at: row.position, from: playlist)
              }
              .disabled(locked)
            }
        }
        .onMove { from, to in
          var order = Array((playlist.entries ?? []).indices)
          order.move(fromOffsets: from, toOffset: to)
          Task { await edit(order: order) }
        }
        // Favourites keep the order they were favourited in; the server has no reorder for them.
        .moveDisabled(locked || removing || playlist.favourites == true)
      }
      if playlist.favourites != true {
        // The album pages' settings block. A List (not their ScrollView) keeps
        // swipe to remove and drag to reorder (#158).
        DetailSettings {
          HStack {
            TextField("Name", text: $name).submitLabel(.done)
              .onSubmit { Task { await edit(name: name) } }
            Button("Rename", systemImage: "pencil") { Task { await edit(name: name) } }
              .buttonStyle(.pigSecondary)
              .disabled(locked || name.isEmpty || name == playlist.name)
          }
          // Anchored to its button, so it opens there rather than at the top.
          Button("Delete playlist", systemImage: "trash", role: .destructive) { deleting = true }
            .disabled(locked)
            .confirmationDialog("Delete \(playlist.name)?", isPresented: $deleting) {
              Button("Delete playlist", systemImage: "trash", role: .destructive) {
                Task { await delete() }
              }
            }
        }.listRowSeparator(.hidden).listRowBackground(Color.clear)
      }
    }.listStyle(.plain).pigCanvas().navigationTitle(playlist.name).navigationBarTitleDisplayMode(.inline)
      .toolbar {
        if playlist.favourites != true && empty == nil { EditButton() }
        // The playlist's one menu (D-118), with Download and Add N songs to
        // library. A plain ellipsis, as the mix page's: the toolbar draws the
        // button, and a drawn circle sat inside it as a second shape (#286).
        Menu {
          PlaylistActionsMenu(
            model: model, playlist: playlist, missing: missing.count, addMissing: addMissing)
        } label: {
          Image(systemName: "ellipsis").accessibilityLabel("More actions for \(playlist.name)")
        }
        // The same order wherever it opens (D-118).
        .menuOrder(.fixed)
      }
      // Pull down to refresh replaces the toolbar's Refresh (D-120).
      .refreshable { if !removing { await load() } }
      .task { await load() }
      // A sync changed the library: show the playlist as the server now has it.
      .onChange(of: model.libraryRevision) { if !removing { Task { await load() } } }
      .onChange(of: removing) { _, now in if !now { Task { await load() } } }
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
      var batches: [AcquisitionResult] = []
      for batch in stride(from: 0, to: requested.count, by: 500) {
        let tracks = Array(requested[batch..<min(batch + 500, requested.count)])
        batches.append(try await model.requestNow(tracks))
      }
      // Every batch's answer, paused songs included, in the notice bar: the
      // page's own reload would clear its line (Codex confirmation, batch D).
      model.notifyDone(AcquisitionResult.playlistNotice(added: added, requested: batches))
      reloadAfterEdit = true
    } catch is CancellationError {} catch { model.notify(error) }
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
