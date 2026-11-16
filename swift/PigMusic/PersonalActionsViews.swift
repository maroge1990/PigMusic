import PigAPI
import PigBrand
import PigDomain
import PigSync
import SwiftUI

/// A song's ⋯ pop-up menu (D-112, #229): the same from a row's ⋯ button, a
/// long press on the row and the player's ⋯. Nothing in it scrolls or
/// competes with a swipe, as the Song actions sheet did. Which items show is
/// SongMenu's (PigDomain); this draws them and runs them. A menu can't wait
/// for anything while it opens, so what it needs is looked up before then
/// (SongActionsLookup); results come as a haptic and failures as the app's
/// message line.
struct SongActionsMenu: View {
  let model: AppModel
  /// The song as its row or the player has it.
  let opened: Track
  /// The library's own copy of the song (#211), when found.
  let own: Track?
  /// The library has been looked at for it: until then the menu offers
  /// neither Request nor the library's actions, so Request never shows for
  /// a song the library holds.
  let looked: Bool
  var body: some View {
    let track = SongMenu.resolved(own: own, live: model.liveState(opened))
    let sections = SongMenu.sections(
      for: track, looked: looked, requested: model.isRequested(track.id),
      favourite: model.favourites[track.id], playing: model.player.queue.current != nil)
    ForEach(Array(sections.enumerated()), id: \.offset) { _, items in
      Section {
        ForEach(items, id: \.self) { item($0, track) }
      }
    }
  }
  @ViewBuilder private func item(_ item: SongMenuItem, _ track: Track) -> some View {
    switch item {
    case .playNext:
      Button("Play next", systemImage: "text.line.first.and.arrowtriangle.forward") {
        model.player.enqueue([track], next: true)
        model.confirmations += 1
      }
    case .addToQueue:
      Button("Add to queue", systemImage: "text.line.last.and.arrowtriangle.forward") {
        model.player.enqueue([track])
        model.confirmations += 1
      }
    case .addToPlaylist:
      let playlists = model.playlists.filter { $0.favourites != true }
      Menu("Add to playlist", systemImage: "text.badge.plus") {
        ForEach(playlists) { playlist in
          Button(playlist.name) { Task { await add(track, to: playlist) } }
        }
        if playlists.isEmpty { Text("No playlists yet") }
      }.disabled(!model.canMutate)
    case .favourite(let favourite):
      // Changes at once (#212); a refusal puts it back.
      Button(
        FavouriteStates.action(favourite: favourite),
        systemImage: FavouriteStates.symbol(favourite: favourite)
      ) {
        Task { await changeFavourite(track) }
      }
    case .loadFavourite:
      Button("Load favourite status", systemImage: "star") {
        Task { await model.loadFavourite(track) }
      }
    case .addToLibrary:
      Button("Add to my library", systemImage: "plus") {
        Task { await addToLibrary(track) }
      }.disabled(!model.canMutate)
    case .requestMix:
      Button("Add to my library", systemImage: "arrow.down.circle") {
        Task { await request(track) }
      }.disabled(!model.canMutate)
    case .requested:
      // #206: the same mark as an album page's requested songs.
      Button("Requested", systemImage: "clock") {}.disabled(true)
    case .request:
      // At once, with Undo; sent when the server can be reached (#206).
      Button("Request song", systemImage: "arrow.down.circle") {
        model.requestLater(track)
      }.disabled(!model.canRequest)
    case .download:
      // D-106: the quality is chosen in a pop-up.
      Menu("Download", systemImage: "arrow.down.circle") {
        Button("Original") { Task { await model.download([track]) } }
        Button("Smaller AAC copy") { Task { await model.download([track], quality: "aac") } }
      }
    case .sendToWatch:
      Button("Send to Apple Watch", systemImage: "applewatch") {
        Task { await sendToWatch(track) }
      }
    case .removeFromLibrary:
      // At once, with Undo (D-79, #214, D-108).
      Button("Remove from library", systemImage: "minus.circle", role: .destructive) {
        model.removeFromLibraryLater(track)
      }
    }
  }
  private func changeFavourite(_ track: Track) async {
    do { try await model.tapFavourite(track) } catch is CancellationError {} catch {
      model.message = error.userMessage
    }
  }
  private func add(_ track: Track, to playlist: Playlist) async {
    do {
      try await model.saveEdit(
        OfflineMutation(action: "append", playlistId: playlist.id, track: track))
      model.confirmations += 1
    } catch is CancellationError {} catch { model.message = error.userMessage }
  }
  private func addToLibrary(_ track: Track) async {
    do {
      _ = try await model.performPersonal { try await $0.addMembership(trackIds: [track.id]) }
      model.confirmations += 1
    } catch is CancellationError {} catch { model.message = error.userMessage }
  }
  private func request(_ track: Track) async {
    do {
      let result = try await model.performPersonal { try await $0.requestMusic([track]) }
      if result.downloadsEnabled {
        model.confirmations += 1
      } else {
        model.message = "Requested. Downloads are off on the server, so it will wait."
      }
    } catch is CancellationError {} catch { model.message = error.userMessage }
  }
  /// D-103: a success tap, no text; a failure says why.
  private func sendToWatch(_ track: Track) async {
    switch await model.watch?.enqueue([track]) {
    case .sent: model.confirmations += 1
    case .failed(let reason): model.message = reason
    case .superseded, nil: break
    }
  }
}

/// Looks up what a song's ⋯ menu needs before it can open (#229): the
/// library's own copy (#211), again after each sync, off the main thread in
/// the library store, and the favourite state this iPhone already holds. The
/// server is never asked for every row's favourite; the menu offers to load
/// one it doesn't know.
struct SongActionsLookup<Content: View>: View {
  let model: AppModel
  let track: Track
  @ViewBuilder let content: (SongActionsMenu) -> Content
  @State private var own: Track?
  /// The song the lookup answered; another song (the player's next) waits
  /// for its own.
  @State private var looked: String?
  var body: some View {
    let found = looked == track.id
    content(SongActionsMenu(model: model, opened: track, own: found ? own : nil, looked: found))
      .task(id: Key(id: track.id, revision: model.libraryRevision)) {
        let copy = await model.ownCopy(of: track)
        guard !Task.isCancelled else { return }
        own = copy
        looked = track.id
        await model.peekFavourite(SongMenu.resolved(own: copy, live: model.liveState(track)))
      }
  }
  private struct Key: Hashable {
    let id: String
    let revision: Int
  }
}

/// Server requests, one per album request (a lone song alone), unfinished
/// first, under Transfers as on the iPhone and Watch pages (#209): swipe a
/// song to cancel it with Undo; no explanatory text (D-101). Opening a
/// request shows its songs with their source and quality.
struct RequestsView: View {
  let model: AppModel
  @State private var groups: [RequestGroup] = []
  @State private var next: Int?
  @State private var loading = false
  @State private var loaded = false
  @State private var enabled = true
  @State private var error: String?
  /// A success to report, apart from errors (Codex round 2 on #159).
  @State private var notice: String?
  var body: some View {
    List {
      if !enabled {
        StatusText("Server downloads are disabled.", kind: .error)
      }
      if let error { StatusText(error, kind: .error) }
      if let notice { StatusText(notice) }
      if loaded && groups.isEmpty { Text("No music requests").foregroundStyle(.secondary) }
      if !groups.isEmpty {
        Section("Transfers") {
          ForEach(groups) { group in
            let items = group.items.filter {
              !model.undo.hides(AppModel.undoKey(cancelRequest: $0.id))
            }
            DisclosureGroup {
              ForEach(items) { item in
                songRow(item)
                  .swipeActions {
                    if Self.cancellable.contains(item.status) {
                      Button("Cancel", role: .destructive) { cancel(item) }
                    }
                  }
              }
            } label: {
              // The album's mark acts on all its songs (D-110).
              TransferGroupLabel(
                model: model, title: group.title, subtitle: group.artist + " · " + group.summary,
                artwork: group.releaseId.map { .release($0) },
                control: TransferGroupControl(items.map { ($0.id, Self.control($0)) }),
                noun: group.songs == 1 ? "song" : "album", progress: group.progress,
                disabled: loading || !model.canMutate
              ) { tap, ids in Task { await act(tap, ids: ids) } }
            }
          }
          if next != nil {
            Button("Show more requests") { Task { await load(reset: false) } }.disabled(loading)
              .buttonStyle(.pigSecondary)
          }
        }
      }
    }.pigCanvas().navigationTitle("Server").navigationBarTitleDisplayMode(.inline)
      .toolbar {
        Button("Clear finished") { Task { await clear() } }
          .disabled(loading || !model.canMutate)
      }
      .task { await load(reset: true) }
      // Live: the server's request queue changed.
      .onChange(of: model.requestsRevision) { Task { await load(reset: true) } }
  }
  private static let cancellable: Set<String> = [
    "waiting_for_sources", "searching", "downloading", "validating", "paused",
  ]
  private static func control(_ item: RequestGroupItem) -> TransferControl {
    TransferControl.server(status: item.status, progress: item.requestProgress)
  }
  /// A song, whose mark is its control (D-109): pause, resume or retry.
  private func songRow(_ item: RequestGroupItem) -> some View {
    HStack(spacing: 4) {
      VStack(alignment: .leading, spacing: 4) {
        Text(item.title)
        Text(
          [
            RequestStatus.label(item.status), item.source.map(RequestSource.name) ?? "",
            item.quality ?? "",
          ]
          .filter { !$0.isEmpty }.joined(separator: " · ")
        ).font(PigType.caption).foregroundStyle(.secondary)
        if let error = item.error, !error.isEmpty {
          StatusText(error, kind: .error)
        }
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .accessibilityElement(children: .combine)
      TransferMarkButton(Self.control(item)) { tap in Task { await act(tap, ids: [item.id]) } }
        .disabled(loading || !model.canMutate)
    }
  }
  /// Pauses, resumes or retries these requests, then reads the list again.
  /// A song that changed first (it finished, say) doesn't stop the others;
  /// signing out or a cancelled task does.
  private func act(_ tap: TransferTap, ids: [String]) async {
    let failure = await TransferBatch.run(
      ids, stops: { $0 is CancellationError || ($0 as? NativeFailure) == .authentication }
    ) { id in
      _ = try await model.performPersonal { service in
        switch tap {
        case .pause: try await service.pauseRequest(id: id)
        case .resume: try await service.resumeRequest(id: id)
        case .retry: try await service.retryRequest(id: id)
        }
      }
    }
    // Read the list first: reading it clears the page's message.
    await load(reset: true)
    if let failure, !(failure is CancellationError) { error = failure.userMessage }
  }
  /// At once, with Undo (D-79): sent when the Undo runs out (#206).
  private func cancel(_ item: RequestGroupItem) {
    notice = nil
    model.cancelRequestLater(item) { continuesForOthers in
      notice =
        continuesForOthers
        ? "Your request was cancelled. The shared download continues for another person." : nil
    }
  }
  private func load(reset: Bool) async {
    guard !loading else { return }
    loading = true
    error = nil
    defer { loading = false }
    let offset = reset ? 0 : (next ?? 0)
    do {
      let page = try await model.readCatalogue { try await $0.requestGroups(offset: offset) }
      if reset {
        groups = page.groups
      } else {
        for group in page.groups {
          if let index = groups.firstIndex(where: { $0.id == group.id }) {
            groups[index] = group
          } else {
            groups.append(group)
          }
        }
      }
      next = page.nextOffset
      enabled = page.downloadsEnabled
      model.serverActiveRequests = page.active
      loaded = true
    } catch is CancellationError {} catch { self.error = error.userMessage }
  }
  private func clear() async {
    guard !loading else { return }
    loading = true
    do {
      _ = try await model.performPersonal { try await $0.clearFinishedRequests() }
      model.requestedSongs.requestsChanged()
    } catch is CancellationError {
      loading = false
      return
    } catch {
      self.error = error.userMessage
      loading = false
      return
    }
    loading = false
    await load(reset: true)
  }
}

/// The playing song's favourite star: filled when it is a favourite (#100).
/// A tap changes it at once, and a library sync never greys it (#212).
struct FavouriteStar: View {
  let model: AppModel
  let track: Track
  @State private var failed = false
  private var favourite: Bool { model.favourites[track.id] ?? false }
  var body: some View {
    RoundIcon(
      label: FavouriteStates.action(favourite: favourite),
      systemImage: FavouriteStates.symbol(favourite: favourite)
    ) {
      Task { await toggle() }
    }
    .disabled(!model.favourites.canTap(track.id))
    .task(id: track.id) { await model.loadFavourite(track) }
    // Also a refusal that comes on a later send (Codex round 2).
    .onChange(of: model.favouriteRefusals) { failed = true }
    .alert("Favourite not saved", isPresented: $failed) {
      Button("OK", role: .cancel) {}
    }
  }
  private func toggle() async {
    do { try await model.tapFavourite(track) } catch is CancellationError {} catch {
      failed = true
    }
  }
}
