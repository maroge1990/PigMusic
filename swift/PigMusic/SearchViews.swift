import PigAPI
import PigBrand
import PigDomain
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
      // The ranked search when the server has it and something is typed;
      // the earlier search before typing (recent picks), offline and on an
      // older server.
      if let found = model.foundSearch, !found.unsupported,
        !SearchText.clean(query).isEmpty, found.error == nil || found.results != nil
      {
        FoundResultsView(model: model, search: found)
      } else if let session = model.searchSession {
        SearchResultsView(model: model, session: session)
      } else {
        ProgressView("Opening library…")
      }
    }
    .navigationTitle("Search")
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
        model.searchSession?.clear()
        model.foundSearch?.clear()
        return
      }
      do {
        try await Task.sleep(for: SearchText.debounce)
        try Task.checkCancellation()
      } catch { return }
      await search(force: false)
    }
    .toolbar {
      Button("Refresh", systemImage: "arrow.clockwise") {
        Task { await search(force: true) }
      }.disabled(SearchText.clean(query).isEmpty)
    }
  }
  /// The ranked search first; the earlier one when it can't answer (an older
  /// server, or offline), which also searches this phone's library.
  private func search(force: Bool) async {
    if let found = model.foundSearch, !found.unsupported {
      await found.search(query, force: force)
      if !found.unsupported && found.error == nil { return }
    }
    await model.searchSession?.search(query, force: force)
  }
  private func focusIfAsked() {
    guard focusRequest != handledFocus else { return }
    handledFocus = focusRequest
    // Presenting in the same update as the tab change can be ignored.
    Task { @MainActor in searching = true }
  }
}

private struct SearchResultsView: View {
  let model: AppModel
  @Bindable var session: SearchSession
  var body: some View {
    List {
      if session.query.isEmpty {
        ShazamSection(model: model)
        if model.recentSearches.picks.isEmpty {
          ContentUnavailableView(
            "Find music", systemImage: "magnifyingglass",
            description: Text("Songs, albums, artists and playlists, wherever they are."))
        } else {
          recent
        }
      } else {
        Section("Your library") {
          status(
            loading: session.localLoading, error: session.localError,
            empty: session.tracks.isEmpty && session.albums.isEmpty && session.artists.isEmpty)
          ForEach(session.artists) { artist in
            NavigationLink {
              SearchArtistView(model: model, artist: artist)
                .onAppear { model.rememberSearch(.artist(artist)) }
            } label: {
              HStack(spacing: 12) {
                SavedArtistThumb(model: model, artist: artist)
                Text(artist.name)
              }
            }
          }
          ForEach(session.albums) { album in
            NavigationLink {
              AlbumView(model: model, album: album)
                .onAppear { model.rememberSearch(.album(album)) }
            } label: {
              HStack(spacing: 12) {
                ArtworkThumb(model: model, source: album.artworkSource, title: album.title)
                VStack(alignment: .leading) {
                  Text(album.title)
                  Text(album.artist).font(PigType.footnote).foregroundStyle(.secondary)
                }
              }
            }
          }
          ForEach(session.tracks) { song in
            SongButton(model: model, track: song) {
              model.rememberSearch(.song(song))
              model.play([song])
            }
          }
          if session.localNext != nil {
            Button("More library results") { Task { await session.moreLocal() } }.disabled(
              session.localLoading)
          }
        }
        if session.onlineEnabled {
          Section("Household songs") {
            status(
              loading: session.householdLoading, error: session.householdError,
              empty: session.household.isEmpty)
            ForEach(session.household) { song in
              SongButton(model: model, track: song) {
                model.rememberSearch(.song(song))
                model.play([song], origin: .catalogue(artistId: nil))
              }
            }
            if session.householdNext != nil {
              Button("More household results") { Task { await session.moreHousehold() } }.disabled(
                session.householdLoading)
            }
          }
          Section("Catalogue artists") {
            if session.query.count < 2 {
              Text("Enter at least two characters.").foregroundStyle(.secondary)
            } else {
              status(
                loading: session.catalogueLoading, error: session.catalogueError,
                empty: session.catalogue.isEmpty)
              ForEach(session.catalogue) { artist in
                NavigationLink {
                  CatalogueArtistView(model: model, artistId: artist.id, name: artist.name)
                    .onAppear {
                      model.rememberSearch(.catalogueArtist(id: artist.id, name: artist.name))
                    }
                } label: {
                  HStack(spacing: 12) {
                    Artwork(model: model, source: .artist(artist.id), title: artist.name, circle: true)
                      .frame(width: 44, height: 44)
                    VStack(alignment: .leading) {
                      Text(artist.name)
                      if !artist.description.isEmpty {
                        Text(artist.description).font(PigType.caption).foregroundStyle(.secondary)
                      }
                    }
                  }
                }
              }
              if session.catalogueNext != nil {
                Button("More catalogue artists") { Task { await session.moreCatalogue() } }
                  .disabled(session.catalogueLoading)
              }
            }
          }
        }
      }
    }
  }
  /// Picks opened or played from search, newest first; tapping one does the
  /// same again (D-61).
  private var recent: some View {
    Section {
      ForEach(model.recentSearches.picks) { pick in
        switch pick {
        case .song(let song):
          SongButton(model: model, track: song) {
            // Played as the library holds it now, so a newer download is used.
            Task { await model.replayRecent(pick) }
          }
        case .album(let album):
          NavigationLink {
            RecentAlbumView(model: model, album: album).onAppear { model.rememberSearch(pick) }
          } label: {
            HStack(spacing: 12) {
              ArtworkThumb(model: model, source: album.artworkSource, title: album.title)
              VStack(alignment: .leading) {
                Text(album.title)
                Text("Album · " + album.artist).font(PigType.footnote).foregroundStyle(.secondary)
              }
            }
          }
        case .artist(let artist):
          NavigationLink {
            SearchArtistView(model: model, artist: artist).onAppear { model.rememberSearch(pick) }
          } label: {
            HStack(spacing: 12) {
              SavedArtistThumb(model: model, artist: artist)
              VStack(alignment: .leading) {
                Text(artist.name)
                Text("Artist").font(PigType.footnote).foregroundStyle(.secondary)
              }
            }
          }
        case .catalogueArtist(let id, let name):
          NavigationLink {
            CatalogueArtistView(model: model, artistId: id, name: name)
              .onAppear { model.rememberSearch(pick) }
          } label: {
            HStack(spacing: 12) {
              Artwork(model: model, source: .artist(id), title: name, circle: true)
                .frame(width: 44, height: 44)
              VStack(alignment: .leading) {
                Text(name)
                Text("Artist").font(PigType.footnote).foregroundStyle(.secondary)
              }
            }
          }
        }
      }
    } header: {
      HStack {
        Text("Recently searched")
        Spacer()
        Button("Clear") { model.clearRecentSearches() }.textCase(nil)
      }
    }
  }
  @ViewBuilder private func status(loading: Bool, error: String?, empty: Bool) -> some View {
    if loading { ProgressView("Searching…") }
    if let error { Text(error).font(PigType.footnote).foregroundStyle(.secondary) }
    if !loading && error == nil && empty { Text("No matches").foregroundStyle(.secondary) }
  }
}

struct SavedArtistThumb: View {
  let model: AppModel
  let artist: Artist
  @State private var sha: String?
  var body: some View {
    Artwork(model: model, source: sha.map { .stored($0) }, title: artist.name, circle: true)
      .frame(width: 44, height: 44)
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
  @State private var songs: [Track] = []
  @State private var cover: String?
  @State private var loading = false
  @State private var more = true
  @State private var error: String?
  @State private var exploring = false
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 14) {
        DetailHeader(
          model: model, source: cover.map { .stored($0) }, eyebrow: "IN YOUR LIBRARY",
          title: artist.name,
          subtitle: artist.albumCount.map { "\($0) album\($0 == 1 ? "" : "s")" }, circle: true
        ).preparesPlayback(songs, model: model)
        HStack(spacing: 10) {
          PlayCapsule { model.play(songs) }.disabled(songs.isEmpty)
          RoundIcon(label: "Shuffle", systemImage: "shuffle") {
            model.play(songs.shuffled())
          }.disabled(songs.isEmpty)
          if model.watch?.supported == true {
            RoundIcon(label: "Send \(artist.name) to Apple Watch", systemImage: "applewatch") {
              Task { await sendToWatch() }
            }.disabled(albums.isEmpty)
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
          }
        }
        if loading { ProgressView().frame(maxWidth: .infinity) }
        if let error { Text(error).font(PigType.footnote) }
        if more && !albums.isEmpty {
          Button("Show more albums") { Task { await load() } }.disabled(loading)
            .buttonStyle(ChipStyle(selected: false)).frame(maxWidth: .infinity)
        }
        if !songs.isEmpty {
          VStack(alignment: .leading, spacing: 0) {
            Text("Songs").font(.title3.weight(.bold)).padding(.bottom, 6)
            ForEach(Array(songs.prefix(10).enumerated()), id: \.element.id) { index, track in
              SongRow(model: model, track: track) { model.play(songs, at: index) }
              Divider()
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
  private func sendToWatch() async {
    var tracks: [Track] = []
    for album in albums { tracks += await model.albumTracks(album) }
    identityMessage = await model.watch?.enqueue(tracks)
  }
  private func loadSongs() async {
    var tracks: [Track] = []
    for album in albums { tracks += await model.albumTracks(album) }
    songs = tracks
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
      ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity).pigCanvas()
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

/// The ranked search (D-61): chips across the top, then one list. Top results
/// mixes every kind; each row is marked by where its music is, never listed
/// apart for it. Updates as the person types.
private struct FoundResultsView: View {
  let model: AppModel
  @Bindable var search: FoundSearch
  @State private var kind: FoundKind = .top
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    let screen = search.screen
    List {
      Section {
        let items = screen[kind]
        ForEach(items) { item in row(item) }
        if items.isEmpty {
          if search.loading {
            ProgressView("Searching…")
          } else {
            Text("No matches").foregroundStyle(.secondary)
          }
        }
        if kind == .top || kind == .songs || kind == .albums, let state = search.online?.state,
          state != "ready"
        {
          Text(
            state == "busy"
              ? "Showing music on the server; more results in a moment."
              : "Music not on the server can't be searched right now."
          ).font(PigType.footnote).foregroundStyle(.secondary)
        }
      } header: {
        chips(screen.kinds).textCase(nil)
      }
    }
    .onChange(of: search.query) { kind = .top }
  }
  private func chips(_ kinds: [FoundKind]) -> some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 8) {
        ForEach(kinds, id: \.self) { option in
          Button(option.rawValue) { kind = option }
            .font(.subheadline.weight(.semibold))
            .padding(.horizontal, 14).padding(.vertical, 7)
            .foregroundStyle(option == kind ? Color.white : PigBrand.text(scheme))
            .background(
              Capsule().fill(option == kind ? PigBrand.accent(scheme) : Color.secondary.opacity(0.15))
            )
            .buttonStyle(.plain)
            .accessibilityAddTraits(option == kind ? .isSelected : [])
        }
      }.padding(.vertical, 4)
    }
  }
  @ViewBuilder private func row(_ item: FoundItem) -> some View {
    switch item {
    case .song(let song):
      SongButton(model: model, track: song) {
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
          album.title, "Album · " + album.artist, mark: mine ? .library : .server)
      }
    case .artist(let artist, let mine):
      NavigationLink {
        SearchArtistView(model: model, artist: artist)
          .onAppear { model.rememberSearch(.artist(artist)) }
      } label: {
        label(
          SavedArtistThumb(model: model, artist: artist), artist.name, "Artist",
          mark: mine ? .library : .server)
      }
    case .playlist(let playlist):
      NavigationLink {
        PlaylistView(model: model, playlist: playlist)
      } label: {
        label(
          Image(systemName: "music.note.list").frame(width: 44, height: 44), playlist.name,
          "Playlist", mark: .library)
      }
    case .mix(let mix):
      NavigationLink {
        MixDetailView(model: model, mix: mix)
      } label: {
        label(
          MixArtwork(kind: mix.kind, title: mix.title).frame(width: 44, height: 44), mix.title,
          "Mix · " + mix.reason, mark: .library)
      }
    case .applePlaylist(let playlist):
      NavigationLink {
        ApplePlaylistView(model: model, playlist: playlist)
      } label: {
        label(
          AppleArtwork(url: playlist.artworkURL).frame(width: 44, height: 44), playlist.name,
          ["Apple Music playlist", playlist.curator].compactMap { $0 }.joined(separator: " · "),
          mark: .apple)
      }
    case .appleSong(let song):
      NavigationLink {
        AppleResultView(model: model, kind: "song", appleId: song.appleId, title: song.title)
      } label: {
        label(
          ArtworkThumb(
            model: model, source: song.albumAppleId.map { .appleAlbum($0) }, title: song.title),
          song.title, "Song · " + song.artist, mark: .elsewhere)
      }
    case .appleAlbum(let album):
      NavigationLink {
        AppleResultView(model: model, kind: "album", appleId: album.appleId, title: album.title)
      } label: {
        label(
          ArtworkThumb(model: model, source: .appleAlbum(album.appleId), title: album.title),
          album.title, "Album · " + album.artist, mark: .elsewhere)
      }
    case .catalogueArtist(let artist):
      NavigationLink {
        CatalogueArtistView(model: model, artistId: artist.id, name: artist.name)
          .onAppear {
            model.rememberSearch(.catalogueArtist(id: artist.id, name: artist.name))
          }
      } label: {
        label(
          Artwork(model: model, source: .artist(artist.id), title: artist.name, circle: true)
            .frame(width: 44, height: 44),
          artist.name, artist.description.isEmpty ? "Artist" : "Artist · " + artist.description,
          mark: .elsewhere)
      }
    }
  }
  /// `apple`: an Apple Music playlist, whose songs are marked one by one.
  private enum Mark { case library, server, elsewhere, apple }
  private func label(_ thumb: some View, _ title: String, _ caption: String, mark: Mark)
    -> some View
  {
    HStack(spacing: 12) {
      thumb
      VStack(alignment: .leading, spacing: 2) {
        Text(title).lineLimit(1)
        Text(caption).font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
        if mark == .elsewhere {
          Text("Not on the server yet").font(PigType.caption).foregroundStyle(.secondary)
        }
      }
      Spacer(minLength: 0)
      // The same marks as songs: a cloud for your library, a faded one for
      // the server; nothing for music not on it yet.
      if mark == .library || mark == .server {
        Image(systemName: "cloud").font(.system(size: 13, weight: .semibold))
          .foregroundStyle(mark == .library ? PigBrand.accent(scheme) : Color.secondary.opacity(0.5))
          .accessibilityLabel(mark == .library ? "In your library" : "On the server")
      }
    }
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
        ContentUnavailableView(
          title, systemImage: "questionmark.circle", description: Text(failure)
        ).pigCanvas()
      } else {
        ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity).pigCanvas()
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
      VStack(alignment: .leading, spacing: 14) {
        DetailHeader(
          model: model, source: album.artworkSource, eyebrow: "ON THE SERVER",
          title: album.title, artist: album.artist)
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
          Text(failure).font(PigType.footnote).foregroundStyle(.secondary)
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
      case nil: ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity).pigCanvas()
      }
    }
    .task(id: album.id) { inLibrary = await model.store?.holds(albumId: album.id) ?? false }
  }
}

/// Apple's own artwork for an Apple Music playlist or song, fetched from Apple.
private struct AppleArtwork: View {
  let url: URL?
  var body: some View {
    AsyncImage(url: url) { image in
      image.resizable().scaledToFill()
    } placeholder: {
      Image(systemName: "music.note.list").foregroundStyle(.secondary)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.secondary.opacity(0.12))
    }
    .clipShape(RoundedRectangle(cornerRadius: 6))
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
      VStack(alignment: .leading, spacing: 14) {
        header
        HStack(spacing: 10) {
          PlayCapsule { model.play(playable, origin: origin) }.disabled(playable.isEmpty)
          RoundIcon(label: "Shuffle", systemImage: "shuffle") {
            model.play(playable.shuffled(), origin: origin)
          }.disabled(playable.isEmpty)
          Spacer()
        }
        if let songs {
          if let summary {
            Text(summary).font(PigType.footnote).foregroundStyle(.secondary)
          }
          LazyVStack(spacing: 0) {
            ForEach(songs) { song in
              row(song)
              Divider()
            }
          }
          if songs.isEmpty {
            Text("This playlist has no songs PigMusic can show.").foregroundStyle(.secondary)
          }
        } else if let failure {
          Text(failure).font(PigType.footnote).foregroundStyle(.secondary)
        } else {
          ProgressView().frame(maxWidth: .infinity)
        }
        if let statusFailure {
          Text(statusFailure).font(PigType.footnote).foregroundStyle(.secondary)
        }
      }.padding(.horizontal).padding(.bottom, 24)
    }
    .pigCanvas().navigationTitle(shown.name).navigationBarTitleDisplayMode(.inline)
    .task(id: playlist.id) { await load() }
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
    HStack(spacing: 12) {
      AppleArtwork(url: song.artworkURL).frame(width: 44, height: 44)
      VStack(alignment: .leading, spacing: 2) {
        Text(song.title).lineLimit(1).foregroundStyle(PigBrand.text(scheme))
        Text(
          [song.artist, song.album ?? ""].filter { !$0.isEmpty }.joined(separator: " · ")
        )
        .font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
        if checked {
          Text("Not on the server yet").font(PigType.caption).foregroundStyle(.secondary)
        }
      }
      Spacer(minLength: 0)
    }
    .frame(minHeight: 48).contentShape(Rectangle())
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

/// Shazam in search (D-70): "What's playing?" identifies a song, and the
/// "Shazamed" list keeps every song identified here. Neither downloads
/// anything by itself.
private struct ShazamSection: View {
  let model: AppModel
  @State private var listening = false
  var body: some View {
    Section {
      Button {
        listening = true
      } label: {
        Label("What's playing?", systemImage: "shazam.logo")
      }
      NavigationLink {
        ShazamInboxView(model: model)
      } label: {
        LabeledContent {
          let count = ShazamListener.shared.inbox.count
          if count > 0 { Text("\(count)") }
        } label: {
          Label("Shazamed", systemImage: "music.note.list")
        }
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
  @State private var found: ShazamSong?
  @State private var failure: ShazamFailure?
  var body: some View {
    NavigationStack {
      Group {
        if let found {
          ShazamFoundView(model: model, song: found, close: close) { attempt += 1 }
        } else if let failure {
          ContentUnavailableView {
            Label("No song found", systemImage: "shazam.logo")
          } description: {
            Text(failure.message)
          } actions: {
            Button("Try again") { attempt += 1 }.pigPrimaryAction()
          }
        } else {
          VStack(spacing: 16) {
            ProgressView().controlSize(.large)
            Text("Listening…").font(.headline)
            Text("Hold your iPhone near the music.").font(PigType.footnote)
              .foregroundStyle(.secondary)
          }
          .frame(maxWidth: .infinity, maxHeight: .infinity)
          .accessibilityElement(children: .combine)
        }
      }
      .pigCanvas().navigationTitle("What's playing?").navigationBarTitleDisplayMode(.inline)
      .toolbar { Button("Done", action: close) }
    }
    .task(id: attempt) {
      found = nil
      failure = nil
      switch await ShazamListener.shared.identify() {
      case .success(let song): found = song
      case .failure(.cancelled): break
      case .failure(let reason): failure = reason
      }
    }
    .onDisappear { ShazamListener.shared.cancel() }
  }
  private func close() {
    ShazamListener.shared.cancel()
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
  @State private var message: String?
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
        if let message { Text(message).font(PigType.footnote) }
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
            .buttonStyle(.bordered).buttonBorderShape(.capsule)
            .disabled(adding || !model.canMutate)
        }
      }
    } else if case .elsewhere = state {
      if let appleId = song.appleId {
        NavigationLink {
          AppleResultView(model: model, kind: "song", appleId: appleId, title: song.title)
        } label: {
          Label("Open in the catalogue", systemImage: "music.note")
        }
        .pigPrimaryAction().buttonBorderShape(.capsule)
        Text("Request it from its catalogue page.").font(PigType.footnote)
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
      message = count > 0 ? "Added to your library." : "Already in your library."
      await check()
    } catch is CancellationError {} catch { message = error.userMessage }
  }
}

/// The "Shazamed" list (D-70): songs identified in PigMusic, newest first,
/// each marked like an Apple playlist song. "Download all missing" requests
/// the ones not on the server only after the person confirms.
private struct ShazamInboxView: View {
  let model: AppModel
  @State private var states: ApplePlaylistStates?
  @State private var statusFailure: String?
  @State private var confirming = false
  @State private var requesting = false
  @State private var requested: Set<String> = []
  @State private var message: String?
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
        ContentUnavailableView(
          "Nothing Shazamed yet", systemImage: "shazam.logo",
          description: Text(
            "Songs you identify with What's playing? in Search, or share to PigMusic from the Shazam app, appear here."
          ))
      } else {
        Section {
          if requesting {
            ProgressView("Sending requests…")
          } else {
            Button("Download all missing (\(missing.count))", systemImage: "arrow.down.circle") {
              confirming = true
            }
            .disabled(missing.isEmpty || !model.canMutate)
            .confirmationDialog(
              "Download \(missing.count) song\(missing.count == 1 ? "" : "s")?",
              isPresented: $confirming, titleVisibility: .visible
            ) {
              Button("Download \(missing.count) song\(missing.count == 1 ? "" : "s")") {
                Task { await request(missing) }
              }
            } message: {
              Text(
                "Your server finds and downloads the songs that aren't on it yet. A song with no exact match in the catalogue is left out."
              )
            }
          }
          if let message { Text(message).font(PigType.footnote) }
          if let statusFailure {
            Text(statusFailure).font(PigType.footnote).foregroundStyle(.secondary)
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
    .pigCanvas().navigationTitle("Shazamed")
    .task { await model.receiveShazamShares() }
    .task(id: ApplePlaylistStates.fingerprint(asked)) { await load(asked) }
  }
  private func row(_ song: ShazamSong, state: ApplePlaylistSongState?) -> some View {
    HStack(spacing: 12) {
      AppleArtwork(url: song.artworkURL).frame(width: 44, height: 44)
      VStack(alignment: .leading, spacing: 2) {
        Text(song.title.isEmpty ? "Unknown song" : song.title).lineLimit(1)
          .foregroundStyle(PigBrand.text(scheme))
        Text(song.artist).font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
        if requested.contains(song.shazamId) {
          Text("Requested").font(PigType.caption).foregroundStyle(.secondary)
        } else if let caption = ShazamStatus.caption(state) {
          Text(caption).font(PigType.caption).foregroundStyle(.secondary)
        }
      }
      Spacer(minLength: 0)
    }
    .frame(minHeight: 48).contentShape(Rectangle())
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
      let outcome = try await model.performPersonal { service in
        try await ShazamRequests.requestMissing(songs, source: service)
      }
      // Marked only when sent: an unmatched song stays missing.
      requested.formUnion(songs.map(\.shazamId).filter { !outcome.unmatched.contains($0) })
      let sent = outcome.created + outcome.existing
      let unmatched = outcome.unmatched.count
      message =
        [
          sent > 0 ? "Requested \(sent) song\(sent == 1 ? "" : "s")." : nil,
          unmatched > 0
            ? "\(unmatched) had no exact match in the catalogue: open \(unmatched == 1 ? "it" : "them") to choose a version."
            : nil,
          outcome.downloadsEnabled ? nil : "Server downloads are disabled; requests will wait.",
        ].compactMap { $0 }.joined(separator: " ")
    } catch is CancellationError {} catch { message = error.userMessage }
  }
}
