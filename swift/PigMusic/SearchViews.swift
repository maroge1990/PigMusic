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
            AlbumView(model: model, album: album).onAppear { model.rememberSearch(pick) }
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
  private enum Mark { case library, server, elsewhere }
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
      if mark != .elsewhere {
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
  var body: some View {
    List {
      Section {
        HStack(spacing: 14) {
          ArtworkThumb(model: model, source: album.artworkSource, title: album.title, size: 88)
          VStack(alignment: .leading, spacing: 4) {
            Text(album.title).font(.headline)
            Text(album.artist).foregroundStyle(.secondary)
            Text("On the server").font(PigType.footnote).foregroundStyle(.secondary)
          }
        }
      }
      Section {
        ForEach(Array(tracks.enumerated()), id: \.element.id) { index, track in
          SongRow(model: model, track: track, number: track.trackNumber ?? index + 1) {
            model.play(tracks, at: index, origin: .catalogue(artistId: nil))
          }
        }
        if let failure {
          Text(failure).font(PigType.footnote).foregroundStyle(.secondary)
        } else if tracks.isEmpty {
          ProgressView()
        }
      }
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
