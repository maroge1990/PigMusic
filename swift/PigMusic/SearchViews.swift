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
      if let session = model.searchSession {
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
        return
      }
      do {
        try await Task.sleep(for: SearchText.debounce)
        try Task.checkCancellation()
      } catch { return }
      await model.searchSession?.search(query)
    }
    .toolbar {
      Button("Refresh", systemImage: "arrow.clockwise") {
        Task { await model.searchSession?.search(query, force: true) }
      }.disabled(SearchText.clean(query).isEmpty)
    }
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
            description: Text("Search your saved library, household music and the catalogue."))
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
            model.rememberSearch(pick)
            model.play(
              [song],
              origin: song.serverState == "household_available"
                ? .catalogue(artistId: nil) : .library)
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
