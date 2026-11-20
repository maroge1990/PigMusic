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
/// notice, on every page (#245).
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
  /// What the row's page adds (D-118): Remove from playlist, the Up next edits.
  var page: [SongPageAction] = []
  var body: some View {
    let track = SongMenu.resolved(own: own, live: model.liveState(opened))
    let sections = SongMenu.sections(
      for: track, looked: looked, requested: model.isRequested(track.id),
      favourite: model.favourites[track.id], playing: model.player.queue.current != nil,
      watch: model.watchPaired, page: page.map(\.item),
      paused: model.requestedSongs.paused.contains(track.id))
    ForEach(Array(sections.enumerated()), id: \.offset) { _, items in
      Section {
        ForEach(items, id: \.self) { item($0, track) }
      }
    }
  }
  @ViewBuilder private func item(_ item: SongMenuItem, _ track: Track) -> some View {
    switch item {
    case .goToAlbum:
      Button("Go to album", systemImage: "square.stack") {
        if let link = SongMenu.album(of: track) {
          model.pageRoute = PageRoute(.album(link, title: track.album ?? track.title))
        }
      }
    case .goToArtist:
      Button("Go to artist", systemImage: "music.microphone") {
        let mine = track.serverState == nil || track.serverState == "in_library"
        let origin: PlayOrigin = mine ? .library : .catalogue(artistId: nil)
        model.pageRoute = PageRoute(.artist(ArtistRoute(track: track, origin: origin)))
      }
    case .page(let pageItem):
      if let action = page.first(where: { $0.item == pageItem }) {
        Button(
          pageItem.label, systemImage: pageItem.symbol,
          role: pageItem.destructive ? .destructive : nil
        ) {
          action.run()
        }
      }
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
    case .favouriteUnknown:
      // Not known on this iPhone (#284): never a load; it makes a favourite.
      Button("Favourite", systemImage: "star") {
        Task { await changeFavourite(track, orFavourite: true) }
      }
    case .addToLibrary:
      Button("Add to library", systemImage: "plus") {
        Task { await addToLibrary(track) }
      }.disabled(!model.canMutate)
    case .requestMix:
      Button("Add to library", systemImage: "plus") {
        Task { await request(track) }
      }.disabled(!model.canMutate)
    case .requested:
      // #206: the same mark as an album page's requested songs.
      Button("Adding", systemImage: "clock") {}.disabled(true)
    case .paused:
      // #264: the row's pause mark; Downloads resumes it.
      Button("Paused", systemImage: "pause.circle") {}.disabled(true)
    case .request:
      // At once, with Undo; sent when the server can be reached (#206).
      Button("Add to library", systemImage: "plus") {
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
      Button("Remove from library", systemImage: "trash", role: .destructive) {
        model.removeFromLibraryLater(track)
      }
    }
  }
  private func changeFavourite(_ track: Track, orFavourite: Bool = false) async {
    do {
      try await model.tapFavourite(track, orFavourite: orFavourite)
    } catch is CancellationError {} catch {
      model.notify(error)
    }
  }
  private func add(_ track: Track, to playlist: Playlist) async {
    do {
      try await model.saveEdit(
        OfflineMutation(action: "append", playlistId: playlist.id, track: track))
      model.confirmations += 1
    } catch is CancellationError {} catch { model.notify(error) }
  }
  private func addToLibrary(_ track: Track) async {
    do {
      _ = try await model.performPersonal { try await $0.addMembership(trackIds: [track.id]) }
      model.confirmations += 1
    } catch is CancellationError {} catch { model.notify(error) }
  }
  private func request(_ track: Track) async {
    do {
      let result = try await model.performPersonal { try await $0.requestMusic([track]) }
      if result.downloadsEnabled {
        model.confirmations += 1
      } else {
        model.notify("Adding. Downloads are off on the server, so it will wait.")
      }
    } catch is CancellationError {} catch { model.notify(error) }
  }
  /// D-103: a success tap, no text; a failure says why.
  private func sendToWatch(_ track: Track) async {
    switch await model.watch?.enqueue([track]) {
    case .sent: model.confirmations += 1
    case .failed(let reason): model.notify(reason)
    case .superseded, nil: break
    }
  }
}

/// The one menu of an album, artist, playlist or mix (D-118, #249): from a
/// long press on its card or row and from the ⋯ on its page. Which items
/// show is CollectionMenu's (PigDomain). The songs are read when an item is
/// chosen, so a card needn't load them to open its menu.
struct CollectionActionsMenu: View {
  let model: AppModel
  let kind: CollectionKind
  /// For "Couldn't load <name>. Try again."
  let name: String
  /// How many songs it has, when known without reading them: none disables
  /// Play, Shuffle and Play next (Codex round 2 on batch D).
  var known: Int?
  /// How many of its songs aren't in your library.
  var missing = 0
  /// A mix card's Show songs (a tap on the card plays the mix).
  var open: (() -> Void)?
  /// Downloads of a playlist are grouped under it on the device.
  var origin: DownloadOrigin = .direct
  /// A playlist isn't offered to itself.
  var playlistId: String?
  var mix: PersonalMix?
  /// Adds the songs not in your library; by default asks for each.
  var addMissing: (() async -> Void)?
  /// After Hide this edition: a mix page closes.
  var hidden: (() -> Void)?
  /// Nil when they couldn't be read.
  let songs: @MainActor () async -> [Track]?
  private var empty: Bool { known == 0 }
  /// Plays only what was read: nothing leaves what plays alone, and a read
  /// that failed says so (Codex round 2 on batch D).
  /// Each action belongs to the account that chose it: one that ended
  /// before or while the songs were read does nothing at all (Codex
  /// confirmation on batch D).
  private func playing(_ act: @escaping @MainActor ([Track]) -> Void) {
    let stamp = model.currentGeneration
    Task {
      let outcome = await CollectionPlay.read(
        name: name, isCurrent: { model.isCurrent(stamp) }, songs: { await songs() })
      switch outcome {
      case .play(let tracks): act(tracks)
      case .nothing: break
      case .notice(let text): model.notify(text)
      }
    }
  }
  /// Add, Download, Send to Apple Watch: the songs read for this account.
  private func withSongs(_ act: @escaping @MainActor ([Track]) async -> Void) {
    let stamp = model.currentGeneration
    Task {
      guard model.isCurrent(stamp) else { return }
      let found = await songs() ?? []
      guard model.isCurrent(stamp) else { return }
      await act(found)
    }
  }
  var body: some View {
    let sections = CollectionMenu.sections(
      kind, missing: missing, watch: model.watchPaired, opens: open != nil)
    ForEach(Array(sections.enumerated()), id: \.offset) { _, items in
      Section {
        ForEach(items, id: \.self) { item($0) }
      }
    }
  }
  @ViewBuilder private func item(_ item: CollectionMenuItem) -> some View {
    switch item {
    case .showSongs:
      Button("Show songs", systemImage: "list.bullet") { open?() }
    case .play:
      Button("Play", systemImage: "play.fill") {
        playing { model.play($0) }
      }.disabled(empty)
    case .shuffle:
      Button("Shuffle", systemImage: "shuffle") {
        playing { model.play($0.shuffled()) }
      }.disabled(empty)
    case .playNext:
      Button("Play next", systemImage: "text.line.first.and.arrowtriangle.forward") {
        playing { tracks in
          if model.player.queue.current == nil {
            model.play(tracks)
          } else {
            model.player.enqueue(tracks, next: true)
            model.confirmations += 1
          }
        }
      }.disabled(empty)
    case .addToLibrary:
      Button("Add \(missing) song\(missing == 1 ? "" : "s") to library", systemImage: "plus") {
        if let addMissing {
          Task { await addMissing() }
        } else {
          withSongs { await model.addToLibrary($0) }
        }
      }.disabled(!model.canMutate)
    case .addToPlaylist:
      let playlists = model.playlists.filter { $0.favourites != true && $0.id != playlistId }
      Menu("Add to playlist", systemImage: "text.badge.plus") {
        ForEach(playlists) { playlist in
          Button(playlist.name) { withSongs { await model.add($0, to: playlist) } }
        }
        if playlists.isEmpty { Text("No playlists yet") }
      }.disabled(!model.canMutate)
    case .download:
      // Worded as a song's Download (D-106, D-118).
      Menu("Download", systemImage: "arrow.down.circle") {
        Button("Original") { withSongs { await model.download($0, origin: origin) } }
        Button("Smaller AAC copy") {
          withSongs { await model.download($0, quality: "aac", origin: origin) }
        }
      }
    case .sendToWatch:
      Button("Send to Apple Watch", systemImage: "applewatch") {
        withSongs { await model.sendToWatch($0, origin: origin) }
      }
    case .saveMix:
      Button("Save this edition", systemImage: "plus") {
        if let mix { Task { await model.saveMixEdition(mix) } }
      }.disabled(!model.canMutate)
    case .hideMix:
      // At once, with Undo (D-79).
      Button("Hide this edition", systemImage: "eye.slash", role: .destructive) {
        if let mix {
          model.hideMixEditionLater(mix)
          hidden?()
        }
      }
    }
  }
}

/// A library album's collection menu: its card's long press and its page's ⋯.
struct AlbumActionsMenu: View {
  let model: AppModel
  let album: Album
  var body: some View {
    CollectionActionsMenu(model: model, kind: .album, name: album.title) {
      await model.readAlbumTracks(album)
    }
  }
}

/// A library artist's collection menu: its card's long press and its page's ⋯.
struct ArtistActionsMenu: View {
  let model: AppModel
  let artist: Artist
  var body: some View {
    CollectionActionsMenu(model: model, kind: .artist, name: artist.name) {
      await model.artistTracks(artist)
    }
  }
}

/// A playlist's collection menu: its row's long press and its page's ⋯. The
/// page passes what it knows of the songs missing from your library.
struct PlaylistActionsMenu: View {
  let model: AppModel
  let playlist: Playlist
  var missing = 0
  var addMissing: (() async -> Void)?
  var body: some View {
    CollectionActionsMenu(
      model: model, kind: .playlist, name: playlist.name,
      known: playlist.entries == nil ? playlist.trackCount : model.shownEntries(playlist).count,
      missing: missing,
      origin: DownloadOrigin(playlistId: playlist.id, playlistName: playlist.name),
      playlistId: playlist.id, addMissing: addMissing
    ) {
      await model.playlistSongs(playlist)
    }
  }
}

extension AppModel {
  /// A playlist's songs: those it holds, else as the server (or this
  /// iPhone's last answer) has them.
  /// Less the songs removed here, waiting for their Undo or already applied,
  /// whichever copy answers (Codex round 1 on batch D).
  func playlistSongs(_ playlist: Playlist) async -> [Track]? {
    var copy = playlist
    if copy.entries == nil {
      let id = playlist.id
      guard
        let found = try? await cachedCatalogue(
          key: "playlist:" + id, conditional: { try await $0.playlist(id: id, etag: $1) })
      else { return nil }
      copy = found
    }
    return shownEntries(copy).map(\.entry.playbackTrack)
  }
}

/// One of the page's own items in a song's menu (D-118), with what it does.
struct SongPageAction {
  let item: SongPageItem
  let run: () -> Void
  init(_ item: SongPageItem, run: @escaping () -> Void) {
    self.item = item
    self.run = run
  }
}

extension SongPageItem {
  var label: String {
    switch self {
    case .removeFromPlaylist: "Remove from playlist"
    case .moveEarlier: "Move earlier"
    case .moveLater: "Move later"
    case .removeFromUpNext: "Remove from Up next"
    }
  }
  var symbol: String {
    switch self {
    case .removeFromPlaylist, .removeFromUpNext: "xmark"
    case .moveEarlier: "arrow.up"
    case .moveLater: "arrow.down"
    }
  }
  var destructive: Bool { self == .removeFromPlaylist || self == .removeFromUpNext }
}

/// Looks up what a song's ⋯ menu needs before it can open (#229): the
/// library's own copy (#211), again after each sync, off the main thread in
/// the library store, and the favourite state this iPhone already holds (its
/// library's Favourites playlist, #284). The server is never asked for every
/// row's favourite, and the menu never offers to load one.
struct SongActionsLookup<Content: View>: View {
  let model: AppModel
  let track: Track
  var page: [SongPageAction] = []
  @ViewBuilder let content: (SongActionsMenu) -> Content
  @State private var own: Track?
  /// The song the lookup answered; another song (the player's next) waits
  /// for its own.
  @State private var looked: String?
  var body: some View {
    let found = looked == track.id
    content(
      SongActionsMenu(
        model: model, opened: track, own: found ? own : nil, looked: found, page: page))
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
        StatusText("Downloads are off on the server.", kind: .error)
      }
      if let error { StatusText(error, kind: .error) }
      if let notice { StatusText(notice) }
      if loaded && groups.isEmpty { Text("Nothing being added").foregroundStyle(.secondary) }
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
                      Button("Cancel", systemImage: "xmark", role: .destructive) { cancel(item) }
                    }
                  }
              }
            } label: {
              // The album's mark acts on all its songs (D-110).
              TransferGroupLabel(
                model: model, title: group.title, subtitle: group.artist + " · " + group.summary,
                artwork: group.releaseId.map { .release($0) },
                control: TransferGroupControl(items.map { ($0.id, Self.control($0)) }), place: .server,
                noun: group.songs == 1 ? "song" : "album", progress: group.progress,
                disabled: loading || !model.canMutate
              ) { tap, ids in Task { await act(tap, ids: ids) } }
            }
          }
          if next != nil {
            Button("Show more") { Task { await load(reset: false) } }.disabled(loading)
              .buttonStyle(.pigSecondary)
          }
        }
      }
    }.pigCanvas().navigationTitle("Server").navigationBarTitleDisplayMode(.inline)
      .toolbar {
        // Only while a finished request is listed, or more are still to load.
        ClearFinishedButton(
          enabled: !loading && model.canMutate
            && (next != nil
              || groups.contains { $0.items.contains { RequestStatus.finished.contains($0.status) } })
        ) { Task { await clear() } }
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
      TransferMarkButton(Self.control(item), on: .server) { tap in Task { await act(tap, ids: [item.id]) } }
        .disabled(loading || !model.canMutate)
    }
  }
  /// Pauses, resumes or retries these requests, then reads the list again.
  /// A song that changed first (it finished, say) doesn't stop the others;
  /// signing out or a cancelled task does.
  private func act(_ tap: TransferTap, ids: [String]) async {
    var done: [String] = []
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
      done.append(id)
    }
    // Only a song whose Resume went through loses its paused mark; other
    // songs' marks stay (Codex round 1 on batch D).
    if tap == .resume {
      let items = groups.flatMap { group in group.items.map { (group, $0) } }
      for (group, item) in items where done.contains(item.id) {
        model.requestedSongs.resumed(
          title: item.title, artist: item.artist ?? group.artist,
          album: item.album ?? (group.songs == 1 ? nil : group.title))
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
        ? "Cancelled for you. Another person is adding it too, so it continues." : nil
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
