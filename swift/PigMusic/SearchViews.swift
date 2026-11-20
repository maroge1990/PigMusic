import PigAPI
import PigBrand
import PigDomain
import PigPlayback
import PigSync
import SwiftUI

struct SearchView: View {
  @Bindable var model: AppModel
  /// Rises each time the Search tab is chosen: the box takes the cursor and
  /// the keyboard opens (D-61). Coming back from a result doesn't.
  var focusRequest = 0
  @State private var query = ""
  @State private var searching = false
  @State private var handledFocus = 0
  var body: some View {
    Group {
      // Recent picks before typing; then the ranked search, which searches
      // this phone's library when the server can't answer (#177).
      if let found = model.foundSearch {
        if SearchText.clean(query).isEmpty {
          SearchStartView(model: model)
        } else {
          FoundResultsView(model: model, search: found)
        }
      } else {
        ProgressView("Opening library…")
      }
    }
    .navigationTitle("Search")
    .toolbar { ToolbarItem(placement: .topBarTrailing) { SearchMenu(model: model) } }
    // Always shown, never hidden until the page is pulled down.
    .searchable(
      text: $query, isPresented: $searching,
      placement: .navigationBarDrawer(displayMode: .always),
      prompt: "Songs, albums and artists"
    )
    .onAppear(perform: focusIfAsked)
    .onChange(of: focusRequest) { focusIfAsked() }
    .task { await model.loadRecentSearches() }
    .task(id: "\(model.artworkNamespace)|\(SearchText.clean(query))") {
      // Debounce typing; the previous results stay until new ones arrive.
      if SearchText.clean(query).isEmpty {
        model.foundSearch?.clear()
        return
      }
      do {
        try await Task.sleep(for: SearchText.debounce)
        try Task.checkCancellation()
      } catch { return }
      await search(force: false)
    }
  }
  private func search(force: Bool) async {
    await model.foundSearch?.search(query, force: force)
    model.noteSearchStates()
  }
  private func focusIfAsked() {
    guard focusRequest != handledFocus else { return }
    handledFocus = focusRequest
    // Presenting in the same update as the tab change can be ignored.
    Task { @MainActor in searching = true }
  }
}

/// Before anything is typed (D-120): What's playing?, recent searches you
/// can clear, then shortcuts into your library with counts.
private struct SearchStartView: View {
  let model: AppModel
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 24) {
        ShazamSection(model: model)
        let picks = model.recentSearches.picks.filter { pick in
          // Removed from the library, waiting for its Undo (D-108).
          if case .song(let song) = pick { return !model.undo.removing(song) }
          return true
        }
        if !picks.isEmpty {
          VStack(alignment: .leading, spacing: 4) {
            SectionHeading(title: "Recent") {
              Button("Clear") { model.clearRecentSearches() }.font(PigType.subhead)
            }
            VStack(spacing: 0) {
              ForEach(picks) { pick in
                HStack(spacing: 4) {
                  row(pick)
                  Button {
                    model.forgetSearch(pick)
                  } label: {
                    Image(systemName: "xmark").font(.footnote.weight(.semibold))
                      .foregroundStyle(.secondary).frame(width: 36, height: 44)
                  }
                  .buttonStyle(.plain)
                  .accessibilityLabel("Remove " + Self.name(pick))
                }
                RowSeparator()
              }
            }
          }
        }
        VStack(alignment: .leading, spacing: 10) {
          SectionHeading("Your library")
          LazyVGrid(
            columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible())], spacing: 10
          ) {
            shortcut("Artists", "person", model.artistCount) { LibraryArtistsView(model: model) }
            shortcut("Albums", "square.stack", model.albumCategoryCounts.values.reduce(0, +)) {
              LibraryAlbumsView(model: model)
            }
            shortcut("Songs", "music.note", model.songCount) { LibrarySongsView(model: model) }
            shortcut("Playlists", "music.note.list", model.playlists.count) {
              PlaylistsView(model: model)
            }
          }
        }
      }
      .padding(.horizontal).padding(.vertical, 8)
    }
    .scrollDismissesKeyboard(.immediately)
  }
  private static func name(_ pick: RecentPick) -> String {
    switch pick {
    case .song(let song): song.title
    case .album(let album): album.title
    case .artist(let artist): artist.name
    case .catalogueArtist(_, let name): name
    }
  }
  /// A pick opened or played from search; tapping it does the same again (D-61).
  @ViewBuilder private func row(_ pick: RecentPick) -> some View {
    switch pick {
    case .song(let song):
      SongRow(model: model, track: song) {
        // Played as the library holds it now, so a newer download is used.
        Task { await model.replayRecent(pick) }
      }
    case .album(let album):
      NavigationLink {
        RecentAlbumView(model: model, album: album).onAppear { model.rememberSearch(pick) }
      } label: {
        CollectionRow(album.title, subtitle: "Album · " + album.artist) {
          ArtworkThumb(model: model, source: album.artworkSource, title: album.title)
        }.frame(minHeight: 52)
      }.buttonStyle(.plain)
    case .artist(let artist):
      NavigationLink {
        SearchArtistView(model: model, artist: artist).onAppear { model.rememberSearch(pick) }
      } label: {
        CollectionRow(artist.name, subtitle: "Artist") {
          SavedArtistThumb(model: model, artist: artist)
        }.frame(minHeight: 52)
      }.buttonStyle(.plain)
    case .catalogueArtist(let id, let name):
      NavigationLink {
        CatalogueArtistView(model: model, artistId: id, name: name)
          .onAppear { model.rememberSearch(pick) }
      } label: {
        CollectionRow(name, subtitle: "Artist") {
          ArtworkThumb(model: model, source: .artist(id), title: name, circle: true)
        }.frame(minHeight: 52)
      }.buttonStyle(.plain)
    }
  }
  private func shortcut<Destination: View>(
    _ title: String, _ symbol: String, _ count: Int,
    @ViewBuilder destination: @escaping () -> Destination
  ) -> some View {
    NavigationLink {
      destination()
    } label: {
      VStack(alignment: .leading, spacing: 6) {
        Image(systemName: symbol).font(.title3).foregroundStyle(PigBrand.accent(scheme))
        Text(title).font(.body.weight(.semibold)).foregroundStyle(PigBrand.text(scheme))
        Text(count.formatted()).font(PigType.footnote).pigFigures().foregroundStyle(.secondary)
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(14)
      .background(
        PigBrand.surface(scheme), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
    .buttonStyle(.plain)
    .accessibilityElement(children: .ignore)
    .accessibilityLabel(title)
    .accessibilityValue(count.formatted())
    .accessibilityAddTraits(.isButton)
  }
}

struct SavedArtistThumb: View {
  let model: AppModel
  let artist: Artist
  @State private var sha: String?
  var body: some View {
    ArtworkThumb(
      model: model, source: artist.artworkSource(cover: sha), title: artist.name, circle: true)
      .task(id: artist.id) { sha = await model.artistCover(artist) }
  }
}

/// Saved artist: compact header, album grid, then settings.
struct SavedArtistView: View {
  let model: AppModel
  let artist: Artist
  @State private var identity: ArtistIdentity?
  @State private var identityMessage: String?
  @State private var albums: [Album] = []
  /// Owned songs, album by album, for Play and the Songs list.
  @State private var owned: [Track] = []
  /// Less a song removed from the library, waiting for its Undo (D-108).
  private var songs: [Track] { model.undo.withoutRemoved(owned) }
  @State private var cover: String?
  @State private var loading = false
  @State private var more = true
  @State private var error: String?
  @State private var exploring = false
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: PigSpacing.page) {
        DetailHeader(
          model: model, source: artist.artworkSource(cover: cover), eyebrow: "IN YOUR LIBRARY",
          title: artist.name,
          subtitle: artist.albumCount.map { "\($0) album\($0 == 1 ? "" : "s")" }, circle: true,
          tinted: true
        ).preparesPlayback(songs, model: model)
        HStack(spacing: 10) {
          PlayCapsule { model.play(songs) }.disabled(songs.isEmpty)
          RoundIcon(label: "Shuffle", systemImage: "shuffle") {
            model.play(songs.shuffled())
          }.disabled(songs.isEmpty)
          DownloadMenu(model: model, tracks: songs)
          CollectionMenuButton(label: "More actions for \(artist.name)") {
            ArtistActionsMenu(model: model, artist: artist)
          }
          Spacer()
        }
        LazyVGrid(columns: cardColumns, spacing: 18) {
          ForEach(albums) { album in
            NavigationLink {
              AlbumView(model: model, album: album)
            } label: {
              CollectionCard(
                model: model, source: album.artworkSource, title: album.title,
                caption: album.year?.text)
            }.buttonStyle(.plain)
              .contextMenu { AlbumActionsMenu(model: model, album: album) }
          }
        }
        if loading { ProgressView().frame(maxWidth: .infinity) }
        if let error { StatusText(error, kind: .error) }
        if more && !albums.isEmpty {
          Button("Show more albums") { Task { await load() } }.disabled(loading)
            .buttonStyle(.pigSecondary).frame(maxWidth: .infinity)
        }
        if !songs.isEmpty {
          VStack(alignment: .leading, spacing: 0) {
            Text("Songs").font(PigType.sectionTitle).padding(.bottom, 6)
            ForEach(Array(songs.prefix(10).enumerated()), id: \.element.id) { index, track in
              SongRow(model: model, track: track) { model.play(songs, at: index) }
              RowSeparator()
            }
          }
        }
        Button {
          Task { await explore() }
        } label: {
          // The library page plays what you have; this finds what you could have.
          DiscographyLinkLabel(title: "Find more by \(artist.name)")
        }
        .buttonStyle(.plain)
        if let identityMessage {
          Text(identityMessage).font(PigType.footnote).foregroundStyle(.secondary)
        }
        DetailSettings {
          InlineQualityPicker(model: model, scope: "artist", id: artist.id)
        }
      }.padding(.horizontal).padding(.bottom, 24)
    }
    .pigCanvas().navigationTitle(artist.name).navigationBarTitleDisplayMode(.inline)
    .navigationDestination(isPresented: $exploring) {
      if let identity {
        CatalogueArtistView(model: model, artistId: identity.id, name: identity.name)
      }
    }
    .task {
      if albums.isEmpty { await load() }
      cover = await model.artistCover(artist)
    }
    // A song removed from the library stays gone once its Undo is sent (D-108).
    .onChange(of: model.libraryRevision) { Task { await loadSongs() } }
  }
  private func explore() async {
    if identity == nil {
      do {
        identity = try await model.cachedCatalogue(key: "identity:" + artist.id) {
          try await $0.artistIdentity(id: artist.id)
        }
        identityMessage = nil
      } catch {
        identityMessage = error.userMessage
        return
      }
    }
    exploring = identity != nil
  }
  private func loadSongs() async {
    var tracks: [Track] = []
    for album in albums { tracks += await model.albumTracks(album) }
    owned = tracks
  }
  private func load() async {
    guard !loading, let store = model.store else { return }
    let namespace = model.artworkNamespace
    loading = true
    error = nil
    defer { loading = false }
    do {
      let found = try await store.albums(artistId: artist.id, offset: albums.count)
      try Task.checkCancellation()
      guard namespace == model.artworkNamespace else { return }
      albums += found
      more = found.count == 100
      await loadSongs()
    } catch is CancellationError {} catch { self.error = error.userMessage }
  }
}

/// A library artist chosen in Search opens the full artist page, which links back
/// to the library page. Without a known catalogue identity it opens the library page.
struct SearchArtistView: View {
  let model: AppModel
  let artist: Artist
  @State private var identity: ArtistIdentity?
  @State private var unresolved = false
  init(model: AppModel, artist: Artist) {
    self.model = model
    self.artist = artist
    _identity = State(initialValue: model.peekCatalogue(key: "identity:" + artist.id))
  }
  var body: some View {
    if let identity {
      CatalogueArtistView(model: model, artistId: identity.id, name: identity.name)
    } else if unresolved {
      SavedArtistView(model: model, artist: artist)
    } else {
      PigLoading("Opening \(artist.name)…").pigCanvas()
        .navigationTitle(artist.name).navigationBarTitleDisplayMode(.inline)
        .task {
          if let found = await model.storedCatalogue(
            key: "identity:" + artist.id, as: ArtistIdentity.self)
          {
            identity = found
          }
          do {
            identity = try await model.cachedCatalogue(key: "identity:" + artist.id) {
              try await $0.artistIdentity(id: artist.id)
            }
          } catch is CancellationError {} catch {
            if identity == nil { unresolved = true }
          }
        }
    }
  }
}

/// The ranked search (D-61), grouped (D-120): one Top result, then Songs,
/// Albums, Artists and Playlists, each item once with its one mark. The
/// Apple Music question waits in a quiet row under the search box. Updates
/// as the person types.
private struct FoundResultsView: View {
  @ScaledMetric(relativeTo: .footnote) private var markSize: CGFloat = 13
  let model: AppModel
  @Bindable var search: FoundSearch
  /// All the songs, not only the first few.
  @State private var allSongs = false
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    let groups = FoundGroups(search.screen)
    List {
      AppleMusicRow(account: model.person?.id) {
        await search.search(search.query, force: true)
      }
      if let top = groups.top, shows(top) {
        Section { row(top) } header: { heading("Top result") }
      }
      let songs = groups.songs.filter(shows)
      if !songs.isEmpty {
        Section {
          ForEach(allSongs ? songs : Array(songs.prefix(FoundGroups.songLimit))) { row($0) }
          if !allSongs && songs.count > FoundGroups.songLimit {
            Button("Show all \(songs.count) songs") { allSongs = true }
              .foregroundStyle(PigBrand.textAccent(scheme))
          }
        } header: {
          heading("Songs")
        }
      }
      group("Albums", groups.albums)
      group("Artists", groups.artists)
      group("Playlists", groups.playlists)
      Section {
        if groups.isEmpty {
          if search.loading {
            // The shared status line, as "No matches" below; a labelled
            // ProgressView set its words in the larger body size (#290).
            HStack(spacing: 8) {
              ProgressView()
              StatusText("Searching…")
            }
            .accessibilityElement(children: .combine)
          } else if let error = search.error {
            StatusText(error, kind: .error)
          } else {
            StatusText("No matches")
          }
        }
        if search.fromThisPhone {
          Text("The server can't be reached, so this is music on this iPhone.")
            .font(PigType.footnote).foregroundStyle(.secondary)
        }
        if let state = search.online?.state, state != "ready" {
          Text(
            state == "busy"
              ? "Showing music on the server; more results in a moment."
              : "Music not on the server can't be searched right now."
          ).font(PigType.footnote).foregroundStyle(.secondary)
        }
      }
    }
    .onChange(of: search.query) { allSongs = false }
  }
  /// Less a song removed from the library, waiting for its Undo (D-108).
  private func shows(_ item: FoundItem) -> Bool {
    guard case .song(let song) = item else { return true }
    return !model.undo.removing(song)
  }
  @ViewBuilder private func group(_ title: String, _ items: [FoundItem]) -> some View {
    if !items.isEmpty {
      Section { ForEach(items) { row($0) } } header: { heading(title) }
    }
  }
  private func heading(_ title: String) -> some View {
    Text(title).font(PigType.sectionTitle).foregroundStyle(PigBrand.text(scheme)).textCase(nil)
  }
  @ViewBuilder private func row(_ item: FoundItem) -> some View {
    switch item {
    case .song(let song):
      SongRow(model: model, track: song) {
        model.rememberSearch(.song(song))
        model.play(
          [song],
          origin: song.serverState == "household_available" ? .catalogue(artistId: nil) : .library)
      }
    case .album(let album, let mine):
      NavigationLink {
        Group {
          if mine {
            AlbumView(model: model, album: album)
          } else {
            // On the server, not in the library: its songs come from the
            // server, with or without a catalogue release (Codex).
            ServerAlbumView(model: model, album: album)
          }
        }.onAppear { model.rememberSearch(.album(album)) }
      } label: {
        label(
          ArtworkThumb(model: model, source: album.artworkSource, title: album.title),
          album.title, "Album · " + album.artist, item: item)
      }
    case .artist(let artist, let mine):
      NavigationLink {
        SearchArtistView(model: model, artist: artist)
          .onAppear { model.rememberSearch(.artist(artist)) }
      } label: {
        label(
          SavedArtistThumb(model: model, artist: artist), artist.name, "Artist",
          item: item)
      }
    case .playlist(let playlist):
      NavigationLink {
        PlaylistView(model: model, playlist: playlist)
      } label: {
        label(
          RowThumb {
            CoverMosaicView(
              model: model, tracks: playlist.songs, favourites: playlist.favourites == true,
              width: 128)
          }, playlist.name, playlist.facts, item: item)
      }
    case .mix(let mix):
      NavigationLink {
        MixDetailView(model: model, mix: mix)
      } label: {
        label(
          RowThumb { MixArtwork(kind: mix.kind, title: mix.title) }, mix.title,
          "Mix · " + mix.reason, item: item)
      }
    case .applePlaylist(let playlist):
      NavigationLink {
        ApplePlaylistView(model: model, playlist: playlist)
      } label: {
        label(
          RowThumb { AppleArtwork(url: playlist.artworkURL) }, playlist.name,
          ["Apple Music playlist", playlist.curator].compactMap { $0 }.joined(separator: " · "),
          item: item)
      }
    case .appleSong(let song):
      NavigationLink {
        AppleResultView(model: model, kind: "song", appleId: song.appleId, title: song.title)
      } label: {
        label(
          ArtworkThumb(
            model: model, source: song.albumAppleId.map { .appleAlbum($0) }, title: song.title),
          song.title, "Song · " + song.artist, item: item)
      }
    case .appleAlbum(let album):
      NavigationLink {
        AppleResultView(model: model, kind: "album", appleId: album.appleId, title: album.title)
      } label: {
        label(
          ArtworkThumb(model: model, source: .appleAlbum(album.appleId), title: album.title),
          album.title, "Album · " + album.artist, item: item)
      }
    case .catalogueArtist(let artist):
      NavigationLink {
        CatalogueArtistView(model: model, artistId: artist.id, name: artist.name)
          .onAppear {
            model.rememberSearch(.catalogueArtist(id: artist.id, name: artist.name))
          }
      } label: {
        label(
          ArtworkThumb(model: model, source: .artist(artist.id), title: artist.name, circle: true),
          artist.name, artist.description.isEmpty ? "Artist" : "Artist · " + artist.description,
          item: item)
      }
    }
  }
  /// The row's one mark (D-120): in your library, adding, paused, or not in
  /// your library yet; nothing for music on the server that streams.
  private func label(_ thumb: some View, _ title: String, _ caption: String, item: FoundItem)
    -> some View
  {
    CollectionRow(title, subtitle: caption) {
      thumb
    } trailing: {
      if let symbol = Self.symbol(item.mark) {
        Image(systemName: symbol).font(.system(size: markSize, weight: .semibold))
          .foregroundStyle(
            item.mark == .inLibrary ? PigBrand.accent(scheme) : PigBrand.secondary(scheme)
          )
          .accessibilityLabel(item.mark.label ?? "")
      }
    }
  }
  static func symbol(_ mark: FoundMark) -> String? {
    switch mark {
    case .inLibrary: "checkmark.circle.fill"
    case .adding: "clock"
    case .paused: "pause.circle"
    case .add: "plus"
    case .none: nil
    }
  }
}

/// The Apple Music question (D-120): a quiet row under the search box, never
/// a prompt mid-typing. Allow asks iOS once (and waits while iOS asks); Not
/// now hides it for this account for 30 days, and the Search menu's switch
/// brings it back (Codex round 1 on batch E).
private struct AppleMusicRow: View {
  let account: String?
  let searchAgain: () async -> Void
  @AppStorage(AppleMusicAsk.storageKey) private var stored = Data()
  @State private var answered = false
  @State private var asking = false
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    let ask = AppleMusicAsk.read(stored)
    if !answered, ask.shows(account: account, undetermined: AppleMusicCatalogue.undetermined) {
      HStack(spacing: 10) {
        Image(systemName: "music.note").foregroundStyle(PigBrand.accent(scheme))
          .accessibilityHidden(true)
        Text("Include Apple Music results").font(PigType.subhead)
        Spacer(minLength: 4)
        Button("Allow") {
          asking = true
          Task {
            let allowed = await AppleMusicCatalogue.ask()
            asking = false
            answered = true
            if allowed { await searchAgain() }
          }
        }
        .disabled(asking)
        .buttonStyle(.borderless).font(PigType.subhead.weight(.semibold))
        .foregroundStyle(PigBrand.textAccent(scheme))
        Button {
          guard let account else { return }
          var changed = ask
          changed.dismiss(account: account)
          stored = changed.data
        } label: {
          Image(systemName: "xmark").font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
        }
        .buttonStyle(.borderless)
        .accessibilityLabel("Not now")
        .disabled(asking)
      }
    }
  }
}

extension AppleMusicAsk {
  static let storageKey = "search.appleMusicAsk"
  static func read(_ data: Data) -> AppleMusicAsk {
    (try? JSONDecoder().decode(AppleMusicAsk.self, from: data)) ?? AppleMusicAsk()
  }
  var data: Data { (try? JSONEncoder().encode(self)) ?? Data() }
}

/// The Search screen's ⋯ (Codex round 1 on batch E): the lasting switch for
/// Apple Music results. On asks iOS when nobody has been asked and brings
/// the row back; otherwise iOS's Settings decides, so it opens there.
private struct SearchMenu: View {
  let model: AppModel
  @AppStorage(AppleMusicAsk.storageKey) private var stored = Data()
  @State private var allowed = AppleMusicCatalogue.allowed
  @Environment(\.openURL) private var openURL
  var body: some View {
    Menu {
      Toggle(
        "Include Apple Music results",
        isOn: Binding(
          get: { allowed },
          set: { on in
            if on, let account = model.person?.id {
              var ask = AppleMusicAsk.read(stored)
              ask.reopen(account: account)
              stored = ask.data
            }
            if on && AppleMusicCatalogue.undetermined {
              Task { allowed = await AppleMusicCatalogue.ask() }
            } else if let settings = URL(string: UIApplication.openSettingsURLString) {
              openURL(settings)
            }
          })
      )
    } label: {
      Image(systemName: "ellipsis").accessibilityLabel("Search options")
    }
    .onAppear { allowed = AppleMusicCatalogue.allowed }
  }
}

/// An Apple result opens as the exact MusicBrainz version (#103): a song on
/// its album's page, an album as itself. Nothing opens for an unmatched one.
private struct AppleResultView: View {
  let model: AppModel
  let kind: String
  let appleId: Int
  let title: String
  @State private var resolved: ResolvedApple?
  @State private var failure: String?
  var body: some View {
    Group {
      if let resolved, kind == "album" {
        CatalogueAlbumView(
          model: model, groupId: resolved.id, title: resolved.name, artist: resolved.artist)
      } else if let resolved, let release = resolved.releaseId {
        CatalogueReleaseView(
          model: model, releaseId: release, title: resolved.album ?? resolved.name, groupId: nil,
          editionCount: 0)
      } else if let failure {
        PigQuietState(title, line: failure)
          .frame(maxHeight: .infinity).pigCanvas()
      } else {
        PigLoading("Opening \(title)…").pigCanvas()
      }
    }
    .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
    .task(id: appleId) {
      do {
        let value = try await model.readCatalogue {
          try await $0.resolveApple(kind: kind, appleId: appleId)
        }
        if let value, kind == "album" || value.releaseId != nil {
          resolved = value
        } else {
          failure = "PigMusic couldn't find this exact version in its music catalogue yet."
        }
      } catch is CancellationError {} catch { failure = error.userMessage }
    }
  }
}

/// A server album outside the person's library, opened from search: its
/// playable songs from the server, played as household music.
private struct ServerAlbumView: View {
  let model: AppModel
  let album: Album
  @State private var tracks: [Track] = []
  @State private var failure: String?
  // The same page as a library album (#62).
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: PigSpacing.page) {
        DetailHeader(
          model: model, source: album.artworkSource, eyebrow: "ON THE SERVER",
          title: album.title, artist: album.artist, tinted: true)
        HStack(spacing: 10) {
          PlayCapsule { model.play(tracks, origin: .catalogue(artistId: nil)) }
            .disabled(tracks.isEmpty)
          RoundIcon(label: "Shuffle", systemImage: "shuffle") {
            model.play(tracks.shuffled(), origin: .catalogue(artistId: nil))
          }.disabled(tracks.isEmpty)
          Spacer()
        }
        AlbumSongList(model: model, tracks: tracks) {
          model.play(tracks, at: $0, origin: .catalogue(artistId: nil))
        }
        if let failure {
          StatusText(failure, kind: .error)
        } else if tracks.isEmpty {
          ProgressView().frame(maxWidth: .infinity)
        }
      }.padding(.horizontal).padding(.bottom, 24)
    }
    .pigCanvas().navigationTitle(album.title).navigationBarTitleDisplayMode(.inline)
    .task(id: album.id) {
      do {
        tracks = try await model.readCatalogue { try await $0.serverAlbum(id: album.id) }.tracks
          ?? []
        if tracks.isEmpty { failure = "This album has no playable songs right now." }
      } catch is CancellationError {} catch { failure = error.userMessage }
    }
  }
}

/// A recent album: from the library when it holds it, else from the server,
/// as it opened from the results (Codex round 2 on stage 4).
private struct RecentAlbumView: View {
  let model: AppModel
  let album: Album
  @State private var inLibrary: Bool?
  var body: some View {
    Group {
      switch inLibrary {
      case true?: AlbumView(model: model, album: album)
      case false?: ServerAlbumView(model: model, album: album)
      case nil: PigLoading("Opening \(album.title)…").pigCanvas()
      }
    }
    .task(id: album.id) { inLibrary = await model.store?.holds(albumId: album.id) ?? false }
  }
}

/// Apple's own artwork for an Apple Music playlist or song, fetched from Apple.
private struct AppleArtwork: View {
  @Environment(\.colorScheme) private var scheme
  let url: URL?
  var body: some View {
    AsyncImage(url: url) { image in
      image.resizable().scaledToFill()
    } placeholder: {
      Image(systemName: "music.note.list").foregroundStyle(.secondary)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(PigBrand.raised(scheme))
    }
    .clipShape(RoundedRectangle(cornerRadius: PigRadius.artwork))
    .accessibilityHidden(true)
  }
}

/// An Apple Music playlist (#109, D-66): its songs in Apple's order, each
/// marked like search: in your library, on the server, or not on the server
/// yet. Songs on the server play from it; one not on it opens its catalogue
/// page, where it can be requested. Opening it never requests anything.
private struct ApplePlaylistView: View {
  let model: AppModel
  let playlist: ApplePlaylist
  @State private var info: ApplePlaylist?
  @State private var songs: [ApplePlaylistSong]?
  @State private var states: [ApplePlaylistSongState]?
  @State private var failure: String?
  @State private var statusFailure: String?
  /// Apple lists more songs than the screen shows (and one press adds).
  @State private var moreThanShown = false
  @State private var adding = false
  @State private var opened: Playlist?
  @State private var toOpen: Playlist?
  @Environment(\.colorScheme) private var scheme
  private var shown: ApplePlaylist { info ?? playlist }
  /// The songs on the server, in the playlist's order.
  private var playable: [Track] { (states ?? []).compactMap(\.track) }
  private var origin: PlayOrigin {
    (states ?? []).contains { if case .server = $0 { true } else { false } }
      ? .catalogue(artistId: nil) : .library
  }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: PigSpacing.page) {
        header
        HStack(spacing: 10) {
          PlayCapsule { model.play(playable, origin: origin) }.disabled(playable.isEmpty)
          RoundIcon(label: "Shuffle", systemImage: "shuffle") {
            model.play(playable.shuffled(), origin: origin)
          }.disabled(playable.isEmpty)
          Spacer()
        }
        // One label: the playlist list doesn't say which Apple playlist a
        // copy came from, so the app can't offer "Update my copy". Adding it
        // again updates the copy on the server (D-69).
        Button("Add to my playlists", systemImage: "plus") { adding = true }
          .buttonStyle(.pigSecondary)
          .disabled((songs ?? []).isEmpty || !model.canMutate)
        if let songs {
          if let summary {
            Text(summary).font(PigType.footnote).foregroundStyle(.secondary)
          }
          LazyVStack(spacing: 0) {
            ForEach(songs) { song in
              row(song)
              RowSeparator()
            }
          }
          if songs.isEmpty {
            Text("This playlist has no songs PigMusic can show.").foregroundStyle(.secondary)
          }
        } else if let failure {
          StatusText(failure, kind: .error)
        } else {
          ProgressView().frame(maxWidth: .infinity)
        }
        if let statusFailure {
          StatusText(statusFailure, kind: .error)
        }
      }.padding(.horizontal).padding(.bottom, 24)
    }
    .pigCanvas().navigationTitle(shown.name).navigationBarTitleDisplayMode(.inline)
    .task(id: playlist.id) { await load() }
    .sheet(
      isPresented: $adding,
      onDismiss: {
        // The new playlist opens once the sheet has gone.
        opened = toOpen
        toOpen = nil
      }
    ) {
      AppleImportSheet(
        model: model, playlist: shown, songs: songs ?? [], states: states,
        moreThanShown: moreThanShown
      ) { added in
        toOpen = added
        adding = false
      }
    }
    .navigationDestination(item: $opened) { PlaylistView(model: model, playlist: $0) }
  }
  private var header: some View {
    HStack(alignment: .center, spacing: 16) {
      AppleArtwork(url: shown.artworkURL).frame(width: 112, height: 112)
        .shadow(color: .black.opacity(0.12), radius: 8, y: 4)
      VStack(alignment: .leading, spacing: 4) {
        Text("APPLE MUSIC PLAYLIST").font(.caption.weight(.bold)).tracking(1.2)
          .foregroundStyle(PigBrand.textAccent(scheme))
        Text(shown.name).font(.title2.weight(.bold)).lineLimit(3)
          .accessibilityAddTraits(.isHeader)
        if let curator = shown.curator, !curator.isEmpty {
          Text(curator).font(PigType.subhead).foregroundStyle(.secondary).lineLimit(2)
        }
      }
      Spacer(minLength: 0)
    }
    .accessibilityElement(children: .combine)
  }
  private var summary: String? {
    guard let states, !states.isEmpty else { return nil }
    let here = states.filter { $0.track != nil }.count
    return "\(here) of \(states.count) songs on the server"
  }
  @ViewBuilder private func row(_ song: ApplePlaylistSong) -> some View {
    let state = states.flatMap { $0.indices.contains(song.position) ? $0[song.position] : nil }
    if let track = state?.track {
      SongRow(model: model, track: track) {
        if let index = playable.firstIndex(of: track) {
          model.play(playable, at: index, origin: origin)
        }
      }
    } else if let appleId = song.appleId {
      NavigationLink {
        AppleResultView(model: model, kind: "song", appleId: appleId, title: song.title)
      } label: {
        elsewhere(song, checked: state != nil)
      }.buttonStyle(.plain)
    } else {
      elsewhere(song, checked: state != nil)
    }
  }
  /// A song not on the server: Apple's details, and the way to its catalogue
  /// page. Unmarked until the server has been asked.
  private func elsewhere(_ song: ApplePlaylistSong, checked: Bool) -> some View {
    // The one song row's slots, without the menu: nothing here to act on (D-118).
    SongRowLabel(
      title: song.title, caption: SongRowFacts.caption(artist: song.artist, album: song.album)
    ) {
      RowThumb { AppleArtwork(url: song.artworkURL) }
    } note: {
      if checked {
        Text("Not on the server yet").font(PigType.caption).foregroundStyle(.secondary)
      }
    }
  }
  private func load() async {
    if songs == nil {
      do {
        guard let found = try await AppleMusicCatalogue.songs(playlistId: playlist.id) else {
          failure = "Apple Music isn't available on this iPhone right now."
          return
        }
        info = found.0
        songs = found.1
        moreThanShown = found.more
      } catch is CancellationError {
        return
      } catch {
        failure = "Apple Music couldn't open this playlist. Try again shortly."
        return
      }
    }
    guard let songs, !songs.isEmpty else { return }
    let key = "apple-playlist:" + playlist.id
    // A saved answer is used only for exactly these songs in this order:
    // Apple may have changed the playlist since (Codex).
    if states == nil,
      let saved = model.peekCatalogue(key: key, as: ApplePlaylistStates.self)?.states(for: songs)
    {
      states = saved
    }
    do {
      let found = try await model.cachedCatalogue(key: key) { service in
        try await ApplePlaylistStatuses.states(for: songs, source: service)
      }
      if let fresh = found.states(for: songs) {
        states = fresh
        statusFailure = nil
      } else if states == nil {
        statusFailure = "Which songs are on the server can't be checked right now."
      }
    } catch is CancellationError {} catch {
      if states == nil {
        statusFailure = "Which songs are on the server can't be checked right now."
      }
    }
  }
}

/// "Add to my playlists" for an Apple playlist (#121, D-69): how many songs
/// are on the server and how many would download, then Add only, or Add and
/// download after a confirmation. The copy is the person's own and doesn't
/// follow Apple's later changes; adding it again updates it.
private struct AppleImportSheet: View {
  let model: AppModel
  let playlist: ApplePlaylist
  let songs: [ApplePlaylistSong]
  let states: [ApplePlaylistSongState]?
  let moreThanShown: Bool
  let open: (Playlist) -> Void
  @State private var confirming = false
  @State private var working = false
  @State private var result: AppleImportResult?
  @State private var message: StatusMessage?
  @Environment(\.dismiss) private var dismiss
  private var capped: Bool { moreThanShown || songs.count > AppleImport.limit }
  var body: some View {
    let choice = states.map(AppleImport.choice)
    let missing = choice?.toDownload ?? 0
    let songsLabel = "\(missing) song\(missing == 1 ? "" : "s")"
    NavigationStack {
      List {
        Section {
          Text(playlist.name).font(.headline)
          if let result {
            Text(AppleImport.summary(result))
          } else {
            if let choice {
              Text("\(choice.onServer) on the server, \(choice.toDownload) not on the server yet")
            }
            if capped {
              Text(
                "This playlist has more than \(AppleImport.limit) songs: the first \(AppleImport.limit) are added."
              )
              .font(PigType.footnote).foregroundStyle(.secondary)
            }
            Text(
              "It becomes your own playlist in Apple's order. Adding it again later updates your copy."
            )
            .font(PigType.footnote).foregroundStyle(.secondary)
          }
          if let message { StatusText(message) }
        }
        Section {
          if working {
            ProgressView("Adding…")
          } else if let result {
            Button("Open playlist", systemImage: "music.note.list") {
              open(model.playlists.first { $0.id == result.playlist.id } ?? result.playlist)
            }
          } else {
            Button("Add only", systemImage: "plus") { Task { await add(download: false) } }
              .disabled(!model.canMutate)
            if missing > 0 {
              Button("Also add \(songsLabel) to library", systemImage: "plus") {
                confirming = true
              }
              .disabled(!model.canMutate)
              .confirmationDialog(
                "Add \(songsLabel) to library?", isPresented: $confirming, titleVisibility: .visible
              ) {
                Button("Add \(songsLabel) to library") { Task { await add(download: true) } }
              } message: {
                Text(
                  "PigMusic finds the songs that aren't on the server yet and adds them to your library; they join the playlist once added."
                )
              }
            }
          }
        }
      }
      .navigationTitle(result == nil ? "Add to my playlists" : "Added")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) {
          Button(result == nil ? "Cancel" : "Done") { dismiss() }
        }
      }
    }
    .presentationDetents([.medium, .large])
    .interactiveDismissDisabled(working)
  }
  private func add(download: Bool) async {
    guard !working else { return }
    working = true
    message = nil
    defer { working = false }
    let playlist = playlist
    let songs = songs
    let moreThanShown = moreThanShown
    do {
      // performPersonal refreshes the person's playlists once it succeeds.
      result = try await model.performPersonal { service in
        try await AppleImports.add(
          playlist, songs: songs, moreThanShown: moreThanShown, downloadMissing: download,
          source: service)
      }
    } catch is CancellationError {} catch { message = .error(error.userMessage) }
  }
}

/// Shazam in search (D-70): "What's playing?" identifies a song, and the
/// "Shazamed" list keeps every song identified here. Neither downloads
/// anything by itself.
private struct ShazamSection: View {
  let model: AppModel
  @State private var listening = false
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    VStack(spacing: 4) {
      Button {
        DiagnosticsLog.shared.record("shazam", "What's playing? tapped")
        listening = true
      } label: {
        Label("What's playing?", systemImage: "shazam.logo")
          .font(.body.weight(.semibold))
          .foregroundStyle(PigBrand.textAccent(scheme))
          .frame(maxWidth: .infinity, minHeight: 48)
          .background(
            PigBrand.surface(scheme), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
      }
      .buttonStyle(.plain)
      let count = ShazamListener.shared.inbox.count
      if count > 0 {
        NavigationLink {
          ShazamInboxView(model: model)
        } label: {
          LabeledContent {
            Text("\(count)")
          } label: {
            Label("Shazamed", systemImage: "music.note.list")
          }
          .frame(minHeight: 44)
        }
        .buttonStyle(.plain)
      }
    }
    .sheet(isPresented: $listening) { ShazamListenView(model: model) }
  }
}

/// Listens once on opening; the microphone permission is asked on the
/// first try only. A failure says why and offers another try.
private struct ShazamListenView: View {
  let model: AppModel
  @Environment(\.dismiss) private var dismiss
  @State private var attempt = 0
  /// This screen, so closing it ends only its own attempt (#124).
  @State private var owner = UUID()
  /// Done was tapped: the attempt it ends says nothing while the screen goes.
  @State private var closing = false
  @State private var found: ShazamSong?
  @State private var failure: ShazamFailure?
  /// PigMusic's song paused for listening (#124).
  @State private var listeningPause: ListeningPause?
  private var offersResume: Bool {
    listeningPause?.offersResume(
      now: model.player.playbackMark, playing: model.player.playing) ?? false
  }
  var body: some View {
    NavigationStack {
      Group {
        if let found {
          ShazamFoundView(model: model, song: found, close: close) { attempt += 1 }
        } else if let failure {
          PigQuietState("No song found", line: failure.message) {
            Button("Try again", systemImage: "arrow.clockwise") { attempt += 1 }.pigPrimaryAction()
          }.frame(maxHeight: .infinity)
        } else {
          VStack(spacing: 16) {
            ProgressView().controlSize(.large)
            Text("Listening…").font(.headline)
          }
          .frame(maxWidth: .infinity, maxHeight: .infinity)
          .accessibilityElement(children: .combine)
        }
      }
      .safeAreaInset(edge: .bottom) {
        if (found != nil || failure != nil) && offersResume {
          Button("Resume playback", systemImage: "play.fill") {
            listeningPause = nil
            model.player.resume()
          }
          .buttonStyle(.pigSecondary).padding()
        }
      }
      .pigCanvas().navigationTitle("What's playing?").navigationBarTitleDisplayMode(.inline)
      .toolbar { Button("Done", action: close) }
    }
    .task(id: attempt) {
      DiagnosticsLog.shared.record("shazam", "listening screen started try \(attempt + 1)")
      found = nil
      failure = nil
      // Listening records through PigMusic's own audio session and would
      // hear PigMusic's song (#124): it pauses, and never plays on by itself
      // (a pause or interruption meanwhile must stand); "Resume playback" is
      // offered while nothing else has changed playback.
      let player = model.player
      if player.playing {
        player.pause()
        listeningPause = ListeningPause(after: player.playbackMark)
      }
      let owner = owner
      // A step the listening limit doesn't cover never leaves the screen on
      // "Listening…": past the outer limit it stops and says so (#124).
      let end = await ShazamListening.bounded(ShazamListening.outerLimit) {
        await ShazamListener.shared.identify(owner: owner, playbackHolds: {
          player.audioSessionActive
        })
      }
      if case .timedOut = end {
        DiagnosticsLog.shared.record(
          "shazam", "no answer within \(ShazamListening.outerLimit): stopped")
        ShazamListener.shared.cancel(owner: owner)
      }
      // Silent only when this screen closed or started another try; any
      // other ending shows the song or the reason with Try again.
      switch ShazamScreen.after(end, screenClosed: Task.isCancelled || closing) {
      case .found(let song)?: found = song
      case .failed(let reason)?: failure = reason
      case nil: break
      }
    }
    .onDisappear { ShazamListener.shared.cancel(owner: owner) }
  }
  private func close() {
    closing = true
    ShazamListener.shared.cancel(owner: owner)
    dismiss()
  }
}

/// Where a Shazam song is, as the server's matching policy decides.
private enum ShazamStatus {
  static func caption(_ state: ApplePlaylistSongState?) -> String? {
    switch state {
    case .library?: "In your library"
    case .server?: "On the server"
    case .elsewhere?: "Not on the server yet"
    case nil: nil
    }
  }
  static func origin(_ state: ApplePlaylistSongState?) -> PlayOrigin {
    if case .server? = state { .catalogue(artistId: nil) } else { .library }
  }
}

/// The identified song: Apple's artwork and names, where it is, and what
/// can be done: play it, add it to the library, or open its catalogue page,
/// where it can be requested.
private struct ShazamFoundView: View {
  let model: AppModel
  let song: ShazamSong
  let close: () -> Void
  let again: () -> Void
  @State private var state: ApplePlaylistSongState?
  @State private var checkFailure: String?
  @State private var message: StatusMessage?
  @State private var adding = false
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    ScrollView {
      VStack(spacing: 14) {
        AppleArtwork(url: song.artworkURL).frame(width: 200, height: 200)
          .shadow(color: .black.opacity(0.12), radius: 8, y: 4)
        VStack(spacing: 4) {
          Text(song.title.isEmpty ? "Unknown song" : song.title)
            .font(.title2.weight(.bold)).multilineTextAlignment(.center)
            .accessibilityAddTraits(.isHeader)
          Text([song.artist, song.album ?? ""].filter { !$0.isEmpty }.joined(separator: " · "))
            .font(PigType.subhead).foregroundStyle(.secondary).multilineTextAlignment(.center)
          if let caption = ShazamStatus.caption(state) {
            Text(caption).font(PigType.footnote.weight(.semibold))
              .foregroundStyle(PigBrand.textAccent(scheme))
          } else if checkFailure == nil {
            ProgressView().padding(.top, 4)
          }
        }
        actions
        if let checkFailure {
          Text(checkFailure).font(PigType.footnote).foregroundStyle(.secondary)
            .multilineTextAlignment(.center)
        }
        if let message { StatusText(message) }
        if ShazamListener.shared.saveFailed {
          Text("This song couldn't be added to Shazamed.").font(PigType.footnote)
            .foregroundStyle(.secondary)
        }
        Button("Listen again", systemImage: "shazam.logo", action: again).padding(.top, 8)
      }
      .padding(.horizontal).padding(.vertical, 24).frame(maxWidth: .infinity)
    }
    .task(id: song.id) { await check() }
  }
  @ViewBuilder private var actions: some View {
    if let track = state?.track {
      HStack(spacing: 10) {
        PlayCapsule {
          model.play([track], origin: ShazamStatus.origin(state))
          close()
        }
        if case .server = state {
          Button("Add to library", systemImage: "plus") { Task { await add(track) } }
            .buttonStyle(.pigSecondary)
            .disabled(adding || !model.canMutate)
        }
      }
    } else if case .elsewhere = state {
      if let appleId = song.appleId {
        NavigationLink {
          AppleResultView(model: model, kind: "song", appleId: appleId, title: song.title)
        } label: {
          Label("Open in the catalogue", systemImage: "magnifyingglass")
        }
        .pigPrimaryAction().buttonBorderShape(.capsule)
        Text("Add it to your library from its catalogue page.").font(PigType.footnote)
          .foregroundStyle(.secondary)
      } else {
        Text("Apple Music doesn't list this song, so PigMusic can't look it up.")
          .font(PigType.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
      }
    }
  }
  private func check() async {
    checkFailure = nil
    guard song.askable else {
      state = .elsewhere
      return
    }
    let songs = [song.playlistSong(position: 0)]
    do {
      let found = try await model.readCatalogue { service in
        try await ApplePlaylistStatuses.states(for: songs, source: service)
      }
      state = found.states(for: songs)?.first
      if state == nil { checkFailure = "Whether the server has it can't be checked right now." }
    } catch is CancellationError {} catch {
      checkFailure = error.userMessage
    }
  }
  private func add(_ track: Track) async {
    guard !adding else { return }
    adding = true
    defer { adding = false }
    do {
      let count = try await model.performPersonal {
        try await $0.addMembership(trackIds: [track.id])
      }
      message = .info(count > 0 ? "Added to your library." : "Already in your library.")
      await check()
    } catch is CancellationError {} catch { message = .error(error.userMessage) }
  }
}

/// The "Shazamed" list (D-70): songs identified in PigMusic, newest first,
/// each marked like an Apple playlist song. "Add N songs to library" asks
/// the server for the ones not on it only after the person confirms.
private struct ShazamInboxView: View {
  let model: AppModel
  @State private var states: ApplePlaylistStates?
  @State private var statusFailure: String?
  @State private var confirming = false
  @State private var requesting = false
  @State private var requested: Set<String> = []
  @State private var message: StatusMessage?
  @Environment(\.colorScheme) private var scheme
  private static let key = "shazam-inbox"
  var body: some View {
    let songs = Array(ShazamListener.shared.inbox.prefix(ApplePlaylistStatuses.limit))
    let asked = ShazamInbox.playlistSongs(songs)
    let current = states?.states(for: asked)
    let missing = ShazamInbox.missing(songs, states: states).filter {
      !requested.contains($0.shazamId)
    }
    let playable = (current ?? []).compactMap(\.track)
    let origin: PlayOrigin =
      (current ?? []).contains { if case .server = $0 { true } else { false } }
      ? .catalogue(artistId: nil) : .library
    List {
      if songs.isEmpty {
        PigQuietState(
          "Nothing Shazamed yet",
          line:
            "Songs you identify with What's playing? in Search, or share to PigMusic from the Shazam app, appear here."
        ).listRowBackground(Color.clear)
      } else {
        Section {
          if requesting {
            ProgressView("Adding…")
          } else {
            let songsLabel = "\(missing.count) song\(missing.count == 1 ? "" : "s")"
            Button("Add \(songsLabel) to library", systemImage: "plus") {
              confirming = true
            }
            .disabled(missing.isEmpty || !model.canMutate)
            .confirmationDialog(
              "Add \(songsLabel) to library?", isPresented: $confirming, titleVisibility: .visible
            ) {
              Button("Add \(songsLabel) to library") {
                Task { await request(missing) }
              }
            } message: {
              Text(
                "PigMusic finds the songs that aren't on the server yet and adds them to your library. A song with no exact match in the catalogue is left out."
              )
            }
          }
          if let message { StatusText(message) }
          if let statusFailure {
            StatusText(statusFailure, kind: .error)
          }
        }
        Section {
          ForEach(Array(songs.enumerated()), id: \.element.id) { index, song in
            let state = current.flatMap { $0.indices.contains(index) ? $0[index] : nil }
            if let track = state?.track {
              SongRow(model: model, track: track) {
                if let at = playable.firstIndex(of: track) {
                  model.play(playable, at: at, origin: origin)
                }
              }
            } else if let appleId = song.appleId {
              NavigationLink {
                AppleResultView(model: model, kind: "song", appleId: appleId, title: song.title)
              } label: {
                row(song, state: state)
              }
            } else {
              row(song, state: state)
            }
          }
        }
      }
    }
    .pigCanvas().navigationTitle("Shazamed").navigationBarTitleDisplayMode(.inline)
    .task { await model.receiveShazamShares() }
    .task(id: ApplePlaylistStates.fingerprint(asked)) { await load(asked) }
  }
  private func row(_ song: ShazamSong, state: ApplePlaylistSongState?) -> some View {
    // The one song row's slots, without the menu (D-118).
    SongRowLabel(
      title: song.title.isEmpty ? "Unknown song" : song.title,
      caption: SongRowFacts.caption(artist: song.artist, album: nil)
    ) {
      RowThumb { AppleArtwork(url: song.artworkURL) }
    } note: {
      if requested.contains(song.shazamId) {
        Text("Adding").font(PigType.caption).foregroundStyle(.secondary)
      } else if let caption = ShazamStatus.caption(state) {
        Text(caption).font(PigType.caption).foregroundStyle(.secondary)
      }
    }
  }
  private func load(_ songs: [ApplePlaylistSong]) async {
    guard !songs.isEmpty else { return }
    // A saved answer is used only for exactly these songs in this order.
    if states?.states(for: songs) == nil,
      let saved = model.peekCatalogue(key: Self.key, as: ApplePlaylistStates.self),
      saved.states(for: songs) != nil
    {
      states = saved
    }
    do {
      let found = try await model.cachedCatalogue(key: Self.key) { service in
        try await ApplePlaylistStatuses.states(for: songs, source: service)
      }
      if found.states(for: songs) != nil {
        states = found
        statusFailure = nil
      } else if states?.states(for: songs) == nil {
        statusFailure = "Which songs are on the server can't be checked right now."
      }
    } catch is CancellationError {} catch {
      if states?.states(for: songs) == nil {
        statusFailure = "Which songs are on the server can't be checked right now."
      }
    }
  }
  private func request(_ songs: [ShazamSong]) async {
    guard !requesting, !songs.isEmpty else { return }
    requesting = true
    message = nil
    defer { requesting = false }
    do {
      let stamp = model.currentGeneration
      let outcome = try await model.performPersonal { [model] service in
        try await ShazamRequests.requestMissing(songs, source: service) {
          await model.isCurrent(stamp)
        }
      }
      // Marked only when the server took it (#259): an unmatched song, one
      // left out or one left paused stays missing, so it can be added again.
      requested.formUnion(outcome.taken)
      let result = AcquisitionResult(
        created: outcome.created, existing: outcome.existing, skipped: outcome.skipped,
        paused: outcome.paused, downloadsEnabled: outcome.downloadsEnabled)
      let matched = outcome.created + outcome.existing + outcome.skipped + outcome.paused
      let unmatched = outcome.unmatched.count
      message = .info(
        [
          matched > 0 ? result.sentence : nil,
          unmatched > 0
            ? "\(unmatched) had no exact match in the catalogue: open \(unmatched == 1 ? "it" : "them") to choose a version."
            : nil,
          outcome.downloadsEnabled ? nil : "Downloads are off on the server, so they will wait.",
        ].compactMap { $0 }.joined(separator: " "))
    } catch is CancellationError {} catch { message = .error(error.userMessage) }
  }
}
