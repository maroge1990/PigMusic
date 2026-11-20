import PigAPI
import AVKit
import PigBrand
import PigDomain
import PigPlayback
import SwiftUI

/// The album categories of the Albums page; empty ones stay hidden, as on the web.
private enum AlbumCategory: String, CaseIterable, Identifiable {
  case album = "Albums"
  case musical = "Musicals"
  case soundtrack = "Soundtracks"
  case compilation = "Compilations"
  var id: String { rawValue }
  var key: String { String(describing: self) }
}

/// Library (D-120): four tiles with artwork and counts into Artists, Albums,
/// Songs and Playlists, then Recently added.
struct LibraryView: View {
  @Bindable var model: AppModel
  /// An empty library is loading, unreachable or truly empty, and says which.
  @ViewBuilder private var emptyLibrary: some View {
    if model.syncing {
      ProgressView("Loading your music…").frame(maxWidth: .infinity).padding(.vertical, 32)
    } else if model.syncFailed {
      PigQuietState(
        "Can't reach your library",
        line: "Music on this iPhone still plays. Check your connection and try again."
      ) {
        Button("Try again", systemImage: "arrow.clockwise") { Task { await model.refresh() } }
          .pigPrimaryAction()
      }
    } else {
      EmptyPageView(page: .library, systemImage: "music.note.house") { model.openSearch() }
    }
  }
  private var tiles: [GridItem] { [GridItem(.flexible(), spacing: 12), GridItem(.flexible())] }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 24) {
        if let message = model.message {
          StatusText(message).padding(.horizontal)
        }
        if model.songCount == 0 {
          emptyLibrary
        } else {
          LazyVGrid(columns: tiles, spacing: 12) {
            tile("Artists", count: model.artistCount) {
              LibraryArtistsView(model: model)
            } art: {
              ArtistTileArt(model: model, artists: Array(model.artists.prefix(3)))
            }
            tile("Albums", count: model.albumCategoryCounts.values.reduce(0, +)) {
              LibraryAlbumsView(model: model)
            } art: {
              AlbumTileArt(model: model, albums: Array(model.albums.prefix(4)))
            }
            tile("Songs", count: model.songCount) {
              LibrarySongsView(model: model)
            } art: {
              SongsTileArt()
            }
            tile("Playlists", count: model.playlists.count) {
              PlaylistsView(model: model)
            } art: {
              PlaylistTileArt(model: model, playlists: PlaylistOrder.shown(model.playlists))
            }
          }
          .padding(.horizontal)
        }
        if let recent = model.homeContent[.recent]?.albums, !recent.isEmpty {
          RecentlyAddedShelf(model: model, albums: recent)
        }
      }
      .padding(.vertical, 8).padding(.bottom, 16)
    }
    .navigationTitle("Library")
    .refreshable {
      await model.refresh()
      await model.refreshHome(force: true)
    }
    .task(id: model.artworkNamespace) { await model.refreshHome() }
    #if DEBUG
      // Headless UI review: --open-album shows the first album page and
      // --open-artist the first artist's.
      .navigationDestination(item: $debugAlbum) { AlbumView(model: model, album: $0) }
      .navigationDestination(item: $debugArtist) { SavedArtistView(model: model, artist: $0) }
      .task {
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--open-album") {
          while model.albums.isEmpty { try? await Task.sleep(for: .milliseconds(200)) }
          debugAlbum = model.albums.first
        } else if arguments.contains("--open-artist") {
          while model.artists.isEmpty { try? await Task.sleep(for: .milliseconds(200)) }
          debugArtist = model.artists.first
        }
      }
    #endif
  }
  #if DEBUG
    @State private var debugAlbum: Album?
    @State private var debugArtist: Artist?
  #endif
  private func tile<Destination: View, Art: View>(
    _ title: String, count: Int, @ViewBuilder destination: @escaping () -> Destination,
    @ViewBuilder art: () -> Art
  ) -> some View {
    NavigationLink {
      destination()
    } label: {
      LibraryTile(title: title, count: count) { art() }
    }
    .buttonStyle(.plain)
    // Named by the collection alone, its count read after it.
    .accessibilityLabel(title)
    .accessibilityValue(count.formatted())
  }
}

/// One Library tile: artwork above the collection's name and count.
private struct LibraryTile<Art: View>: View {
  let title: String
  let count: Int
  @ViewBuilder let art: Art
  @Environment(\.colorScheme) private var scheme
  @ScaledMetric(relativeTo: .body) private var artHeight: CGFloat = 96
  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      art.frame(maxWidth: .infinity).frame(height: artHeight)
      HStack(alignment: .firstTextBaseline) {
        Text(title).font(.body.weight(.semibold)).foregroundStyle(PigBrand.text(scheme))
        Spacer(minLength: 4)
        Text(count.formatted()).font(PigType.subhead).pigFigures()
          .foregroundStyle(PigBrand.secondary(scheme))
      }
    }
    .padding(12)
    .background(PigBrand.surface(scheme), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    .contentShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
  }
}

/// Three artist portraits overlapping.
private struct ArtistTileArt: View {
  let model: AppModel
  let artists: [Artist]
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    HStack(spacing: -22) {
      ForEach(Array(artists.enumerated()), id: \.element.id) { index, artist in
        ArtistCircle(model: model, artist: artist)
          .frame(width: 64, height: 64)
          .overlay(Circle().strokeBorder(PigBrand.surface(scheme), lineWidth: 3))
          .zIndex(Double(3 - index))
      }
    }
    .accessibilityHidden(true)
  }
}

/// An artist's portrait, from their own album covers.
private struct ArtistCircle: View {
  let model: AppModel
  let artist: Artist
  @State private var sha: String?
  var body: some View {
    Artwork(
      model: model, source: artist.artworkSource(cover: sha), title: artist.name, width: 128,
      circle: true
    )
    .task(id: "\(artist.id)|\(model.artworkRefresh)") { sha = await model.artistCover(artist) }
  }
}

/// Four album covers in a square grid.
private struct AlbumTileArt: View {
  let model: AppModel
  let albums: [Album]
  var body: some View {
    Grid(horizontalSpacing: 4, verticalSpacing: 4) {
      ForEach(0..<2, id: \.self) { row in
        GridRow {
          ForEach(0..<2, id: \.self) { column in
            let index = row * 2 + column
            if index < albums.count {
              Artwork(
                model: model, source: albums[index].artworkSource, title: albums[index].title,
                width: 128)
            } else {
              Color.clear.aspectRatio(1, contentMode: .fit)
            }
          }
        }
      }
    }
    .aspectRatio(1, contentMode: .fit)
    .accessibilityHidden(true)
  }
}

private struct SongsTileArt: View {
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    LinearGradient(
      colors: [PigBrand.accent(scheme).opacity(0.55), PigBrand.accent(scheme)],
      startPoint: .topLeading, endPoint: .bottomTrailing
    )
    .overlay {
      Image(systemName: "music.note").font(.system(size: 34, weight: .semibold))
        .foregroundStyle(.white)
    }
    .clipShape(RoundedRectangle(cornerRadius: PigRadius.artwork, style: .continuous))
    .accessibilityHidden(true)
  }
}

/// Favourites' star beside the first other playlist's cover.
private struct PlaylistTileArt: View {
  let model: AppModel
  let playlists: [Playlist]
  var body: some View {
    HStack(spacing: 4) {
      if let favourites = playlists.first(where: { $0.favourites == true }) {
        CoverMosaicView(model: model, tracks: [], favourites: true, width: 128)
          .id(favourites.id)
      }
      if let other = playlists.first(where: { $0.favourites != true }) {
        CoverMosaicView(model: model, tracks: other.songs, width: 128)
      }
    }
    .frame(maxWidth: .infinity)
    .accessibilityHidden(true)
  }
}

/// Recently added albums as covers (Home and Library, D-120).
struct RecentlyAddedShelf: View {
  let model: AppModel
  let albums: [Album]
  var body: some View {
    Shelf(title: "Recently added") {
      ForEach(albums) { album in
        NavigationLink {
          AlbumView(model: model, album: album)
        } label: {
          CollectionCard(
            model: model, source: album.artworkSource, title: album.title, caption: album.artist
          ).frame(width: 120)
        }.buttonStyle(.plain)
          .contextMenu { AlbumActionsMenu(model: model, album: album) }
      }
    }
  }
}

/// Library: Artists, as cards.
struct LibraryArtistsView: View {
  @Bindable var model: AppModel
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: PigSpacing.page) {
        LibrarySortPicker(model: model, collection: .artists, selected: model.artistSort)
        LazyVGrid(columns: cardColumns, spacing: 18) {
          ForEach(model.artists) { artist in
            NavigationLink {
              SavedArtistView(model: model, artist: artist)
            } label: {
              SavedArtistCard(model: model, artist: artist)
            }.buttonStyle(.plain)
              // The artist's one menu, as on its page (D-118).
              .contextMenu { ArtistActionsMenu(model: model, artist: artist) }
          }
        }
        LibraryMore(model: model, collection: .artists, shown: model.artists.count, total: model.artistCount)
      }
      .padding(.horizontal).padding(.bottom, 24)
    }
    .pigCanvas().navigationTitle("Artists")
    .refreshable { await model.refresh() }
  }
}

/// Library: Albums, by category, as cards.
struct LibraryAlbumsView: View {
  @Bindable var model: AppModel
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: PigSpacing.page) {
        let categories = AlbumCategory.allCases.filter {
          $0 == .album || model.albumCategoryCounts[$0.key, default: 0] > 0
        }
        if categories.count > 1 {
          ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
              ForEach(categories) { item in
                let selected = model.albumCategory == item.key
                Button(item.rawValue) { Task { await model.showAlbums(category: item.key) } }
                  .buttonStyle(ChipStyle(selected: selected))
                  .accessibilityAddTraits(selected ? .isSelected : [])
              }
            }
          }
        }
        LibrarySortPicker(model: model, collection: .albums, selected: model.albumSort)
        LazyVGrid(columns: cardColumns, spacing: 18) {
          ForEach(model.albums) { album in
            NavigationLink {
              AlbumView(model: model, album: album)
            } label: {
              CollectionCard(
                model: model, source: album.artworkSource, title: album.title, caption: album.artist)
            }.buttonStyle(.plain)
              // The album's one menu, as on its page (D-118).
              .contextMenu { AlbumActionsMenu(model: model, album: album) }
          }
        }
        LibraryMore(model: model, collection: .albums, shown: model.albums.count, total: model.albumCount)
      }
      .padding(.horizontal).padding(.bottom, 24)
    }
    .pigCanvas().navigationTitle("Albums")
    .refreshable { await model.refresh() }
  }
}

/// Library: Songs, with Play and Shuffle at the top (D-120).
struct LibrarySongsView: View {
  @Bindable var model: AppModel
  /// Songs, less those removed from the library and waiting for their Undo (#214).
  private var shownSongs: [Track] { model.undo.withoutRemoved(model.songs) }
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 12) {
        LibrarySortPicker(model: model, collection: .tracks, selected: model.songSort)
        Text(CollectionFacts.line(count: model.songCount, seconds: model.songSeconds))
          .font(PigType.subhead).pigFigures().foregroundStyle(.secondary)
        let songs = shownSongs
        // The whole library, not only the pages loaded (Codex round 1).
        PlayShuffleRow {
          Task { await model.playLibrarySongs() }
        } shuffle: {
          Task { await model.playLibrarySongs(shuffled: true) }
        }
        .disabled(songs.isEmpty)
        LazyVStack(spacing: 0) {
          ForEach(songs) { track in
            SongRow(model: model, track: track) {
              Task { await model.playLibrarySongs(from: track) }
            }
            RowSeparator()
          }
        }
        LibraryMore(model: model, collection: .tracks, shown: model.songs.count, total: model.songCount)
      }
      .padding(.horizontal).padding(.bottom, 24)
    }
    .pigCanvas().navigationTitle("Songs")
    .refreshable { await model.refresh() }
  }
}

/// Library: Playlists (D-120): New playlist, then Favourites first, each with
/// its cover mosaic, count and length.
struct PlaylistsView: View {
  @Bindable var model: AppModel
  @State private var creating = false
  @ScaledMetric(relativeTo: .body) private var cover: CGFloat = 64
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    let playlists = PlaylistOrder.shown(model.playlists)
    let empty = EmptyPage.playlists(playlists)
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 0) {
        if empty == nil {
          Button {
            creating = true
          } label: {
            HStack(spacing: 14) {
              RoundedRectangle(cornerRadius: PigRadius.artwork, style: .continuous)
                .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
                .overlay { Image(systemName: "plus").font(.title3.weight(.semibold)) }
                .frame(width: cover, height: cover)
              Text("New playlist").font(.body.weight(.semibold))
              Spacer()
            }
            .foregroundStyle(PigBrand.accent(scheme))
            .frame(minHeight: cover + 16).contentShape(Rectangle())
          }
          .buttonStyle(.plain)
        }
        ForEach(playlists) { playlist in
          NavigationLink {
            PlaylistView(model: model, playlist: playlist)
          } label: {
            HStack(spacing: 14) {
              CoverMosaicView(
                model: model, tracks: playlist.songs, favourites: playlist.favourites == true,
                width: 128
              )
              .frame(width: cover, height: cover)
              VStack(alignment: .leading, spacing: 3) {
                Text(playlist.name).font(.body.weight(.semibold))
                  .foregroundStyle(PigBrand.text(scheme)).lineLimit(2)
                Text(playlist.facts).font(PigType.subhead).pigFigures().foregroundStyle(.secondary)
              }
              Spacer(minLength: 0)
            }
            .frame(minHeight: cover + 16).contentShape(Rectangle())
          }
          .buttonStyle(.plain)
          // The playlist's one menu, as on its page (D-118).
          .contextMenu { PlaylistActionsMenu(model: model, playlist: playlist) }
          RowSeparator()
        }
        if let empty {
          EmptyPageView(page: empty, systemImage: "music.note.list") { creating = true }
        }
      }
      .padding(.horizontal).padding(.bottom, 24)
    }
    .pigCanvas().navigationTitle("Playlists")
    .refreshable { await model.refresh() }
    .navigationDestination(isPresented: $creating) { PlaylistToolsView(model: model) }
  }
}

/// "Show more" under a paged list, which also pages in as it scrolls into view.
private struct LibraryMore: View {
  let model: AppModel
  let collection: LibraryCollection
  let shown: Int
  let total: Int
  var body: some View {
    if shown < total {
      Button("Show more") { Task { await model.more(collection) } }
        .buttonStyle(.pigSecondary)
        .disabled(model.loadingMore || model.syncing)
        .frame(maxWidth: .infinity)
        .onAppear { Task { await model.more(collection) } }
    }
  }
}

/// The one secondary button (#161): an unselected chip.
extension ButtonStyle where Self == ChipStyle {
  static var pigSecondary: ChipStyle { .init(selected: false) }
}

struct ChipStyle: ButtonStyle {
  let selected: Bool
  @Environment(\.colorScheme) private var scheme
  func makeBody(configuration: Configuration) -> some View {
    configuration.label.font(.subheadline.weight(.semibold))
      .padding(.horizontal, 16).frame(minHeight: 38)
      .foregroundStyle(selected ? PigBrand.textAccent(scheme) : PigBrand.text(scheme))
      .background(
        selected ? PigBrand.accent(scheme).opacity(0.14) : PigBrand.surface(scheme),
        in: Capsule()
      )
      .overlay(
        Capsule().strokeBorder(
          selected ? PigBrand.accent(scheme) : PigBrand.controlBorder(scheme), lineWidth: 1)
      )
      .opacity(configuration.isPressed ? 0.7 : 1)
  }
}

/// Album page: compact header, one action row, songs, then settings.
struct AlbumView: View {
  @Bindable var model: AppModel
  let album: Album
  @State private var loaded: [Track] = []
  /// Less a song removed from the library, waiting for its Undo (D-108).
  private var tracks: [Track] { model.undo.withoutRemoved(loaded) }
  /// The release's full tracklist from the catalogue, so a partly owned album
  /// shows its other songs in order, as on the web. Nil offline or unmatched.
  @State private var release: CatalogueRelease?
  @State private var requesting = false
  @State private var requestMessage: String?
  @State private var artist: Artist?
  @State private var showingArtist = false
  /// Owned songs, with the release's other songs (household, requested or
  /// missing) in release order.
  private var rows: [Track] {
    guard let release, !release.songs.isEmpty else { return tracks }
    var owned = tracks
    var merged: [Track] = []
    for song in release.songs {
      if let index = owned.firstIndex(where: { Self.same($0, song.track) }) {
        merged.append(owned.remove(at: index))
      } else {
        var track = song.track
        track.serverState =
          switch song.availability {
          case "in_library", "household_available", "requested": song.availability
          default: "not_in_library"
          }
        merged.append(track)
      }
    }
    // Owned songs the release does not list (another edition's bonus tracks).
    return model.undo.withoutRemoved(merged + owned)
  }
  private var missing: [Track] {
    rows.filter { $0.serverState == "not_in_library" }
  }
  private static func same(_ owned: Track, _ listed: Track) -> Bool {
    if let left = owned.musicbrainzRecordingId, let right = listed.musicbrainzRecordingId {
      return left == right
    }
    return (owned.discNumber ?? 1) == (listed.discNumber ?? 1)
      && owned.trackNumber == listed.trackNumber
      && owned.title.localizedCaseInsensitiveCompare(listed.title) == .orderedSame
  }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: PigSpacing.page) {
        DetailHeader(
          model: model, source: album.artworkSource ?? tracks.first?.artworkSource,
          eyebrow: "IN YOUR LIBRARY", title: album.title, subtitle: albumSubtitle,
          artist: album.artist, openArtist: artist == nil ? nil : { showingArtist = true },
          tinted: true
        ).preparesPlayback(tracks, model: model)
        HStack(spacing: 10) {
          PlayCapsule { model.play(tracks) }.disabled(tracks.isEmpty)
          RoundIcon(label: "Shuffle", systemImage: "shuffle") { model.play(tracks.shuffled()) }
            .disabled(tracks.isEmpty)
          DownloadMenu(model: model, tracks: tracks)
          CollectionMenuButton(label: "More actions for \(album.title)") {
            AlbumActionsMenu(model: model, album: album)
          }
          Spacer()
        }
        let rows = rows
        AlbumSongList(model: model, tracks: rows) { model.play(rows, at: $0) }
        if !missing.isEmpty {
          VStack(alignment: .leading, spacing: 6) {
            Button {
              Task { await requestMissing() }
            } label: {
              Label(
                "Add \(missing.count) song\(missing.count == 1 ? "" : "s") to library",
                systemImage: "plus")
            }
            .buttonStyle(.pigSecondary)
            .disabled(requesting || !model.canMutate)
            if let requestMessage {
              Text(requestMessage).font(PigType.footnote).foregroundStyle(.secondary)
            }
          }
        }
        DetailSettings {
          InlineQualityPicker(model: model, scope: "album", id: album.id)
        }
      }.padding(.horizontal).padding(.bottom, 24)
    }
    .pigCanvas().navigationTitle(album.title).navigationBarTitleDisplayMode(.inline)
    .navigationDestination(isPresented: $showingArtist) {
      if let artist { SavedArtistView(model: model, artist: artist) }
    }
    .task(id: "\(album.id)#\(model.libraryRevision)") {
      loaded = await model.albumTracks(album)
      // The other songs no longer wait behind the artist lookup (#59).
      async let found = model.savedArtist(id: album.artistId, name: album.artist)
      await loadRelease()
      artist = await found
    }
  }
  private func loadRelease() async {
    guard
      let releaseId = album.musicbrainzReleaseId
        ?? tracks.lazy.compactMap(\.musicbrainzReleaseId).first
    else { return }
    // The tracklist saved on this phone shows at once; the server's answer
    // replaces it when it arrives (#59: only owned songs showed until then).
    if release == nil {
      release = await model.storedCatalogue(key: "release:" + releaseId)
    }
    // Offline, the last answer is used; without one the page shows what you own.
    if let fresh = try? await model.cachedCatalogue(
      key: "release:" + releaseId,
      conditional: { try await $0.catalogueRelease(releaseId: releaseId, etag: $1) })
    {
      release = fresh
    }
  }
  private func requestMissing() async {
    guard !requesting else { return }
    requesting = true
    defer { requesting = false }
    do {
      let result = try await model.performPersonal { try await $0.requestMusic(missing) }
      requestMessage = result.sentence
      release = nil
      await loadRelease()
    } catch is CancellationError {} catch { requestMessage = error.userMessage }
  }
  private var albumSubtitle: String {
    let count = tracks.isEmpty ? album.trackCount : tracks.count
    return [
      album.year?.text ?? tracks.first?.year?.text,
      count.map { "\($0) song\($0 == 1 ? "" : "s")" },
    ].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
  }
}

/// Device and Watch download choices behind one round button (D-103).
struct DownloadMenu: View {
  let model: AppModel
  let tracks: [Track]
  /// A playlist's downloads are grouped under it on the device.
  var origin: DownloadOrigin = .direct
  /// Songs not in your library yet; offered last, as a server request.
  var missingCount = 0
  var addMissing: (() async -> Void)?
  /// Rises with each send to the Watch; the button shows a checkmark briefly.
  @State private var sends = 0
  @State private var sent = false
  @State private var watchError: String?
  @Environment(\.accessibilityReduceMotion) private var reduceMotion
  var body: some View {
    HStack(spacing: 10) {
      Menu {
        // Worded as a song's Download menu (D-106, D-118).
        Button("Original", systemImage: "arrow.down.circle") {
          Task { await model.download(tracks, origin: origin) }
        }
        Button("Smaller AAC copy", systemImage: "arrow.down.circle") {
          Task { await model.download(tracks, quality: "aac", origin: origin) }
        }
        // Only while an Apple Watch is paired, as in every menu (D-118).
        if model.watchPaired {
          Button("Send to Apple Watch", systemImage: "applewatch") {
            Task { await sendToWatch() }
          }
        }
        if missingCount > 0, let addMissing, model.canMutate {
          Button(
            "Add \(missingCount) song\(missingCount == 1 ? "" : "s") to library",
            systemImage: "plus"
          ) {
            Task { await addMissing() }
          }
        }
      } label: {
        RoundIconLabel(systemImage: sent ? "checkmark" : "arrow.down")
      }
      .buttonStyle(.plain)
      // One VoiceOver stop named "Download" (#97): a Menu wraps its label in a
      // second, unnamed button, merged into this one here.
      .accessibilityElement(children: .combine)
      .accessibilityLabel("Download")
      .accessibilityAddTraits(.isButton)
      // An empty playlist can still be sent: the Watch follows it as songs are added.
      .disabled(tracks.isEmpty && origin.playlistId == nil)
      .task(id: sends) {
        guard sends > 0 else { return }
        try? await Task.sleep(for: .seconds(1.5))
        guard !Task.isCancelled else { return }
        withAnimation(reduceMotion ? nil : .default) { sent = false }
      }
      if let watchError { StatusText(watchError, kind: .error) }
    }
  }
  private func sendToWatch() async {
    watchError = nil
    switch await model.watch?.enqueue(tracks, origin: origin) {
    case .sent:
      withAnimation(reduceMotion ? nil : .default) { sent = true }
      sends += 1
      model.confirmations += 1
    case .failed(let reason): watchError = reason
    case .superseded, nil: break
    }
  }
}


/// Account (D-120): your initials, name and song count, then the links in
/// groups with an icon each, Refresh library, and Sign out last. Seamless
/// playback and Crossfade live in Preferences; nothing explains itself in a
/// paragraph (D-101).
struct AccountView: View {
  @Bindable var model: AppModel
  @State private var signingOut = false
  /// Your music quality in words, once the server says.
  @State private var quality: String?
  /// Counts answers from Your music quality, so an older read is dropped.
  @State private var qualitySaves = 0
  /// How many devices are signed in, once the server says.
  @State private var devices: Int?
  @Environment(\.colorScheme) private var scheme
  @ScaledMetric(relativeTo: .title) private var avatarSize: CGFloat = 64
  private var name: String { model.person?.displayName ?? model.person?.username ?? "" }
  var body: some View {
    Form {
      Section {
        HStack(spacing: 14) {
          Text(
            name.split(separator: " ").prefix(2).compactMap(\.first).map(String.init).joined()
              .uppercased()
          )
          .font(.title2.weight(.bold)).foregroundStyle(PigBrand.textAccent(scheme))
          .frame(width: avatarSize, height: avatarSize)
          .background(PigBrand.accent(scheme).opacity(0.16), in: Circle())
          .accessibilityHidden(true)
          VStack(alignment: .leading, spacing: 2) {
            Text(name).font(.title2.weight(.bold))
            Text("\(model.songCount.formatted()) song\(model.songCount == 1 ? "" : "s") in your library")
              .font(PigType.subhead).foregroundStyle(.secondary).pigFigures()
          }
        }
        .listRowBackground(Color.clear)
        .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
      }
      Section {
        link("Preferences", symbol: "slider.horizontal.3", tint: PigBrand.accent(scheme)) {
          PreferencesView(model: model)
        }
        link(
          "Your music quality", symbol: "waveform", tint: PigBrand.accent(scheme), value: quality
        ) {
          QualityView(model: model) {
            quality = $0
            qualitySaves += 1
          }
        }
      }
      Section {
        link("Password", symbol: "lock", tint: PigBrand.secondary(scheme)) {
          PasswordView(model: model)
        }
        link(
          "Devices and sessions", symbol: "laptopcomputer.and.iphone",
          tint: PigBrand.secondary(scheme), value: devices.map { $0.formatted() }
        ) {
          SessionsView(model: model)
        }
      }
      Section {
        link(
          "Changes not sent yet", symbol: "arrow.up.circle", tint: .gray,
          badge: model.pendingEdits.count
        ) {
          PendingEditsView(model: model)
        }
        link("Diagnostics", symbol: "waveform.path.ecg", tint: .gray) {
          DiagnosticsView(model: model)
        }
      }
      Section {
        Button("Refresh library", systemImage: "arrow.clockwise") { Task { await model.refresh() } }
          .disabled(model.syncing)
      }
      Section {
        // Asks first, as signing out another device does: it can't be undone (D-79, #183).
        Button("Sign out", role: .destructive) { signingOut = true }
          .frame(maxWidth: .infinity)
          .disabled(model.busy || model.mutationActive)
          .confirmationDialog(
            "Sign out of this iPhone? It will need to sign in again.", isPresented: $signingOut,
            titleVisibility: .visible
          ) {
            Button("Sign out", role: .destructive) { Task { await model.signOut() } }
          }
      }
      if let message = model.message { Section { StatusText(message) } }
    }
    .navigationTitle("Account").navigationBarTitleDisplayMode(.inline)
    .task {
      // Only what the rows show; the rows open without them.
      // A choice saved meanwhile on Your music quality wins over this read.
      let before = qualitySaves
      if let settings = try? await model.readCatalogue({ try await $0.quality() }),
        qualitySaves == before
      {
        quality = QualityChoices(settings).summary
      }
      if let sessions = try? await model.readCatalogue({ try await $0.sessions() }) {
        devices = sessions.count { $0.revokedAt == nil }
      }
    }
  }
  /// A link with its icon tile, and its value or a count badge on the right.
  private func link<Destination: View>(
    _ title: String, symbol: String, tint: Color, value: String? = nil, badge: Int = 0,
    @ViewBuilder destination: @escaping () -> Destination
  ) -> some View {
    NavigationLink(destination: destination) {
      HStack(spacing: 12) {
        Image(systemName: symbol).font(.body.weight(.semibold)).foregroundStyle(.white)
          .frame(width: 30, height: 30)
          .background(tint, in: .rect(cornerRadius: 7))
          .accessibilityHidden(true)
        Text(title)
        Spacer()
        if let value { Text(value).foregroundStyle(.secondary).pigFigures() }
        if badge > 0 {
          Text(badge.formatted()).font(PigType.footnote.weight(.semibold)).pigFigures()
            .foregroundStyle(PigBrand.onAccent(scheme))
            .padding(.horizontal, 7).frame(minWidth: 22, minHeight: 22)
            .background(PigBrand.accent(scheme), in: Capsule())
            .accessibilityLabel("\(badge) change\(badge == 1 ? "" : "s")")
        }
      }
    }
  }
}

/// Integrated mini player. On iOS 26.1+ it is the tab bar's accessory, which the
/// system shows inline beside a minimized tab bar.
struct MiniPlayer: View {
  @Bindable var model: AppModel
  private var player: PlayerController { model.player }
  let open: () -> Void
  @ScaledMetric(relativeTo: .subheadline) private var miniThumb: CGFloat = 32
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    HStack(spacing: 10) {
      Button(action: open) {
        HStack(spacing: 10) {
          // The mini player's own smaller thumb, still growing with the text.
          Artwork(
            model: model, source: player.queue.current?.track.artworkSource,
            title: player.queue.current?.track.title ?? "", width: 128
          ).frame(width: miniThumb, height: miniThumb)
          VStack(alignment: .leading, spacing: 0) {
            Text(player.queue.current?.track.title ?? "").font(.subheadline.weight(.semibold))
              .lineLimit(1)
            Text(status).font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
          }
          Spacer(minLength: 0)
        }.contentShape(Rectangle())
      }
      .buttonStyle(.plain)
      .accessibilityLabel("Now playing: " + (player.queue.current?.track.title ?? ""))
      .accessibilityHint("Opens the player")
      Button(action: player.toggle) {
        Image(systemName: player.wantsPlayback ? "pause.fill" : "play.fill")
          .font(.title3).frame(width: 40, height: 40)
      }.buttonStyle(.plain).accessibilityLabel(player.wantsPlayback ? "Pause" : "Play")
      Button(action: player.next) {
        Image(systemName: "forward.fill").font(.title3).frame(width: 40, height: 40)
      }.buttonStyle(.plain).accessibilityLabel("Next song")
    }
    .padding(.horizontal, 12).padding(.vertical, 4)
    .foregroundStyle(PigBrand.text(scheme))
  }
  private var status: String {
    if player.error != nil { return "Playback problem · tap for options" }
    return player.queue.current?.track.artist ?? ""
  }
}

struct NowPlayingView: View {
  @ScaledMetric(relativeTo: .largeTitle) private var playSize: CGFloat = 64
  @Bindable var model: AppModel
  /// Opens the playing song's artist where playback started (library or catalogue).
  var openArtist: (ArtistRoute) -> Void = { _ in }
  private var player: PlayerController { model.player }
  @Environment(\.dismiss) private var dismiss
  @Environment(\.colorScheme) private var scheme
  /// Dragging updates only this value; one seek happens when the drag ends.
  @State private var scrubbing: Double?
  /// The playing cover's colour behind the player (#199).
  @State private var swatch: CoverRGB?
  // Sections are separate properties so each type-checks quickly.
  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(spacing: 20) {
          artwork
          titles
          progress
          transport
          options
          if let error = player.error { failure(error) }
          // What the song menu's actions said (a Watch that can't take the
          // song, a playlist that failed): shown here, as the menu has no
          // room for it (D-112).
          if let message = model.message {
            StatusText(message).multilineTextAlignment(.center)
          }
          upNext
        }.padding(.horizontal, 24).padding(.bottom, 24)
      }
      .background {
        // Behind the cover and titles, fading to the plain canvas before the
        // controls; fixed while the page scrolls.
        CoverWash(swatch: swatch, solidUntil: 0.35, fadeEnd: 0.7).ignoresSafeArea()
      }
      .task(id: current?.artworkSource) {
        // The cover's own width, so the image comes from memory. A song
        // without a cover gives the brand canvas.
        let next = await model.coverSwatch(current?.artworkSource, width: 1024)
        if !Task.isCancelled { swatch = next }
      }
      .navigationTitle("Now playing").navigationBarTitleDisplayMode(.inline)
      .toolbar { Button("Done") { dismiss() } }
    }.pigBrandedCanvas()
      // The song menus' New playlist while the player is open (#285).
      .modifier(NewPlaylistPrompt(model: model))
  }
  private var current: Track? { player.queue.current?.track }
  private var position: Double { scrubbing ?? player.elapsed }
  private var artwork: some View {
    Artwork(
      model: model, source: current?.artworkSource, title: current?.title ?? "", width: 1024
    )
    .pigContentCorners().frame(maxWidth: 340).padding(.top, 12)
    .shadow(color: .black.opacity(0.18), radius: 16, y: 8)
  }
  private var titles: some View {
    VStack(spacing: 4) {
      // One line each; a long name scrolls (D-65, #108).
      ScrollingLine(text: current?.title ?? "", font: .title2.weight(.bold))
      Button {
        if let current {
          openArtist(ArtistRoute(track: current, origin: model.playOrigin))
          dismiss()
        }
      } label: {
        ScrollingLine(text: current?.artist ?? "", font: .headline)
          .foregroundStyle(PigBrand.textAccent(scheme))
      }
      .buttonStyle(.plain).disabled(current == nil)
      .accessibilityHint("Opens the artist")
    }
  }
  private var progress: some View {
    let upper = max(1, player.duration)
    let value = Binding<Double>(
      get: { min(position, upper) }, set: { scrubbing = $0 })
    return VStack(spacing: 2) {
      Slider(value: value, in: 0...upper) { editing in
        if !editing, let target = scrubbing {
          player.seek(target)
          scrubbing = nil
        }
      }
      .accessibilityLabel("Playback position").accessibilityValue(time(position))
      HStack {
        Text(time(position))
        Spacer()
        Text("-" + time(max(0, player.duration - position)))
      }.font(.caption).pigFigures().foregroundStyle(.secondary)
    }
  }
  private var transport: some View {
    HStack(spacing: 40) {
      Button(action: player.previous) {
        Image(systemName: "backward.fill").font(.title).frame(minWidth: 44, minHeight: 44)
      }.accessibilityLabel("Previous song")
      Button(action: player.toggle) {
        Image(systemName: player.wantsPlayback ? "pause.circle.fill" : "play.circle.fill")
          .font(.system(size: playSize)).frame(minWidth: playSize, minHeight: playSize)
      }.accessibilityLabel(player.wantsPlayback ? "Pause" : "Play")
      Button(action: player.next) {
        Image(systemName: "forward.fill").font(.title).frame(minWidth: 44, minHeight: 44)
      }.accessibilityLabel("Next song")
    }.buttonStyle(.plain)
  }
  /// One row when it fits; at large text sizes the round buttons (which grow
  /// with the text) wrap onto two rows instead of running off the screen
  /// (Codex round 2 on #162).
  private var options: some View {
    ViewThatFits(in: .horizontal) {
      HStack(spacing: 12) {
        playbackModes
        songControls
      }
      VStack(spacing: 12) {
        HStack(spacing: 12) { playbackModes }
        HStack(spacing: 12) { songControls }
      }
    }
  }
  @ViewBuilder private var playbackModes: some View {
    Button {
      player.toggleShuffle()
    } label: {
      RoundIconLabel(systemImage: "shuffle").overlay(ring(player.shuffled))
    }.buttonStyle(.plain).accessibilityLabel("Shuffle")
      .accessibilityValue(player.shuffled ? "On" : "Off")
    Button(action: cycleRepeat) {
      RoundIconLabel(systemImage: player.repeatMode == .one ? "repeat.1" : "repeat")
        .overlay(ring(player.repeatMode != .off))
    }.buttonStyle(.plain).accessibilityLabel("Repeat").accessibilityValue(repeatLabel)
    AirPlayButton().frame(width: 40, height: 40)
  }
  @ViewBuilder private var songControls: some View {
    if let track = current {
      FavouriteStar(model: model, track: track.actionTrack)
      // The same pop-up menu as a song row's ⋯ (D-112, #229).
      SongActionsLookup(model: model, track: track.actionTrack) { menu in
        Menu {
          menu
        } label: {
          RoundIconLabel(systemImage: "ellipsis")
        }.buttonStyle(.plain).menuOrder(.fixed).accessibilityLabel("Song actions")
      }
    }
  }
  private func ring(_ on: Bool) -> some View {
    Circle().strokeBorder(on ? PigBrand.accent(scheme) : .clear, lineWidth: 2)
  }
  private var repeatLabel: String {
    switch player.repeatMode {
    case .off: "Off"
    case .all: "All"
    case .one: "One"
    }
  }
  private func cycleRepeat() {
    switch player.repeatMode {
    case .off: player.repeatMode = .all
    case .all: player.repeatMode = .one
    case .one: player.repeatMode = .off
    }
  }
  private func failure(_ error: String) -> some View {
    VStack(spacing: 8) {
      StatusText(error, kind: .error)
        .multilineTextAlignment(.center)
      HStack {
        Button("Try again", systemImage: "arrow.clockwise") { player.retry() }
        Button("Skip song") { player.skip() }
        if player.seamlessEnabled {
          Button("Use streaming") { player.retry(streaming: true) }
        }
      }.buttonStyle(.pigSecondary)
    }
  }
  private var upNext: some View {
    let layout = player.upNextLayout(model)
    return VStack(alignment: .leading, spacing: 0) {
      HStack(alignment: .firstTextBaseline) {
        Text("Up next").font(PigType.sectionTitle)
        if !layout.nothingNext {
          Text(SongSpan.summary(layout.chosen.map(\.track)))
            .font(PigType.footnote).foregroundStyle(.secondary).pigFigures()
        }
        Spacer()
        NavigationLink("Edit") { QueueEditorView(model: model) }
      }.padding(.bottom, 6)
      if layout.nothingNext {
        NothingNext(model: model, layout: layout).padding(.vertical, 8)
      }
      ForEach(layout.chosen.prefix(50)) { item in
        UpNextRow(model: model, item: item)
        RowSeparator()
      }
      if layout.showsSimilarHeading {
        // Where similar songs begin when the queue ran out (#202, D-120).
        Text(UpNextLayout<PlaybackOccurrence>.similarTitle).font(PigType.sectionTitle)
          .padding(.top, 16).padding(.bottom, 6)
          .frame(maxWidth: .infinity, alignment: .leading)
          .accessibilityAddTraits(.isHeader)
      }
      ForEach(layout.similar.prefix(50)) { item in
        UpNextRow(model: model, item: item)
        RowSeparator()
      }
    }
  }
  private func time(_ seconds: Double) -> String {
    guard seconds.isFinite else { return "0:00" }
    let value = Int(seconds.rounded(.down))
    return String(format: "%d:%02d", value / 60, value % 60)
  }
}

/// A song in Up next: the one song row (D-118), whose tap plays from it (#99)
/// and whose menu adds the queue's own edits.
private struct UpNextRow: View {
  let model: AppModel
  let item: PlaybackOccurrence
  var body: some View {
    SongRow(
      model: model, track: item.track, playing: false,
      page: [
        SongPageAction(.moveEarlier) { model.player.moveUpcoming(item.id, by: -1) },
        SongPageAction(.moveLater) { model.player.moveUpcoming(item.id, by: 1) },
        SongPageAction(.removeFromUpNext) { model.removeFromUpNextLater(item) },
      ]
    ) {
      model.player.jump(to: item.id)
    }
    .accessibilityHint("Plays from this song")
  }
}

extension PlayerController {
  /// Up next less the songs whose removal waits for its Undo (D-79).
  func upcoming(hiding undo: UndoQueue) -> [PlaybackOccurrence] {
    queue.items.dropFirst(queue.index + 1).filter {
      !undo.hides(AppModel.undoKey(upNext: $0.id))
    }
  }
  /// Your songs, then similar songs (D-120).
  @MainActor func upNextLayout(_ model: AppModel) -> UpNextLayout<PlaybackOccurrence> {
    UpNextLayout(upcoming(hiding: model.undo), keepPlaying: model.preferences.keepPlaying) {
      queue.isRecommended($0.id)
    }
  }
}

/// No song of yours follows (D-120): "Nothing next", with Shuffle your
/// library when nothing at all waits. Where Keep playing is on, Then similar
/// songs follows it.
private struct NothingNext: View {
  let model: AppModel
  let layout: UpNextLayout<PlaybackOccurrence>
  var body: some View {
    VStack(spacing: 12) {
      Text(UpNextLayout<PlaybackOccurrence>.nothingNextTitle)
        .font(PigType.headline.weight(.semibold)).foregroundStyle(.secondary)
      if layout.offersShuffle {
        Button(UpNextLayout<PlaybackOccurrence>.shuffleTitle, systemImage: "shuffle") {
          Task { await model.shuffleLibrary() }
        }
        .pigPrimaryAction()
        .disabled(model.songCount == 0)
      }
    }
    .frame(maxWidth: .infinity)
  }
}

private struct AirPlayButton: UIViewRepresentable {
  func makeUIView(context: Context) -> AVRoutePickerView {
    let view = AVRoutePickerView()
    view.prioritizesVideoDevices = false
    return view
  }
  func updateUIView(_ view: AVRoutePickerView, context: Context) {}
}

/// The queue (D-120): the playing song with its cover, Up next with its
/// count and time left, then similar songs as their own section. Songs drag
/// to reorder and swipe to remove at once with Undo (D-118, D-79); an empty
/// queue says Nothing next.
struct QueueEditorView: View {
  let model: AppModel
  @Environment(\.colorScheme) private var scheme
  @ScaledMetric(relativeTo: .body) private var coverSize: CGFloat = 64
  private var player: PlayerController { model.player }
  var body: some View {
    let layout = player.upNextLayout(model)
    List {
      if let current = player.queue.current?.track {
        Section { nowPlaying(current) }
      }
      Section {
        if layout.nothingNext {
          NothingNext(model: model, layout: layout).listRowBackground(Color.clear)
        }
        rows(layout.chosen)
      } header: {
        HStack(alignment: .firstTextBaseline) {
          Text("Up next").font(PigType.sectionTitle).foregroundStyle(PigBrand.text(scheme))
          Spacer()
          if !layout.nothingNext {
            Text(SongSpan.summary(layout.chosen.map(\.track))).font(PigType.footnote).pigFigures()
          }
        }.textCase(nil)
      }
      if layout.showsSimilarHeading {
        Section {
          rows(layout.similar)
        } header: {
          Text(UpNextLayout<PlaybackOccurrence>.similarTitle).font(PigType.sectionTitle)
            .foregroundStyle(PigBrand.text(scheme)).textCase(nil)
        }
      }
    }
    .pigCanvas().navigationTitle("Queue").navigationBarTitleDisplayMode(.inline)
    .toolbar { EditButton() }
  }
  private func nowPlaying(_ track: Track) -> some View {
    HStack(spacing: 14) {
      Artwork(model: model, source: track.artworkSource, title: track.title, width: 256)
        .frame(width: coverSize, height: coverSize)
      VStack(alignment: .leading, spacing: 2) {
        Text("NOW PLAYING").font(PigType.caption.weight(.semibold))
          .foregroundStyle(PigBrand.textAccent(scheme))
        Text(track.title).font(.headline).lineLimit(2)
        Text([track.artist, track.album ?? ""].filter { !$0.isEmpty }.joined(separator: " · "))
          .font(PigType.subhead).foregroundStyle(.secondary).lineLimit(1)
      }
      Spacer(minLength: 0)
    }
    .padding(.vertical, 4)
    .accessibilityElement(children: .combine)
  }
  /// The same row and menu as Up next under the player (#157).
  private func rows(_ shown: [PlaybackOccurrence]) -> some View {
    ForEach(shown) { item in
      UpNextRow(model: model, item: item)
        .swipeActions {
          Button("Remove", systemImage: "xmark", role: .destructive) {
            model.removeFromUpNextLater(item)
          }
        }
    }
    .onMove { from, to in move(shown, from: from, to: to) }
  }
  /// Moves one song of those shown to `to`, by its place among the whole queue.
  private func move(_ shown: [PlaybackOccurrence], from: IndexSet, to: Int) {
    guard let index = from.first, shown.indices.contains(index) else { return }
    let moved = shown[index].id
    let items = player.queue.items
    guard let position = items.firstIndex(where: { $0.id == moved }) else { return }
    // Down: after the song it now follows; up: before the song it now precedes.
    let anchor = to > index ? shown[to - 1] : (shown.indices.contains(to) ? shown[to] : nil)
    guard let anchor, let target = items.firstIndex(where: { $0.id == anchor.id }) else { return }
    player.moveUpcoming(moved, by: target - position)
  }
}

/// The sort menu, its names fitting the tab (D-120).
private struct LibrarySortPicker: View {
  let model: AppModel
  let collection: LibraryCollection
  let selected: LibrarySort
  var body: some View {
    Menu {
      Picker(
        "Sort",
        selection: Binding(
          get: { selected },
          set: { value in
            Task { await model.sortLibrary(collection, by: value) }
          })
      ) {
        ForEach(LibrarySortChoice.options(collection), id: \.self) { option in
          Text(LibrarySortChoice.name(option, in: collection)).tag(option)
        }
      }
    } label: {
      Label(label, systemImage: "arrow.up.arrow.down").font(PigType.footnote)
    }
    .disabled(model.loadingMore || model.syncing)
    .accessibilityLabel("Sort, \(label)")
  }
  private var label: String { LibrarySortChoice.name(selected, in: collection) }
}

/// The artist page the player's artist link opens.
struct ArtistRoute: Identifiable, Hashable {
  let id = UUID()
  /// Names to look for in the library, most specific first.
  let names: [String]
  let origin: PlayOrigin
  init(track: Track, origin: PlayOrigin) {
    var names = [track.artist]
    if let albumArtist = track.albumArtist, !albumArtist.isEmpty, albumArtist != track.artist {
      names.append(albumArtist)
    }
    self.names = names
    self.origin = origin
  }
}

/// A page pushed onto the selected tab from anywhere (D-118): the player's
/// artist link, or a song menu's Go to album or Go to artist.
struct PageRoute: Identifiable, Hashable {
  enum Place: Hashable {
    case artist(ArtistRoute)
    case album(RewindLink, title: String)
  }
  let id = UUID()
  let place: Place
  init(_ place: Place) { self.place = place }
}

struct PageRouteView: View {
  let model: AppModel
  let route: PageRoute
  var body: some View {
    switch route.place {
    case .artist(let artist): ArtistRouteView(model: model, route: artist)
    case .album(let link, let title): RewindLinkView(model: model, link: link, title: title)
    }
  }
}

/// Library playback opens the library artist page; catalogue playback opens the
/// full artist page (by the page's catalogue ID, else the library artist's).
struct ArtistRouteView: View {
  let model: AppModel
  let route: ArtistRoute
  @State private var artist: Artist?
  @State private var searched = false
  var body: some View {
    if case .catalogue(let id?) = route.origin {
      CatalogueArtistView(model: model, artistId: id, name: route.names[0])
    } else if let artist {
      if route.origin == .library {
        SavedArtistView(model: model, artist: artist)
      } else {
        SearchArtistView(model: model, artist: artist)
      }
    } else if searched {
      PigQuietState(
        route.names[0], line: "This artist isn't in your library yet. Search to find them."
      ).frame(maxHeight: .infinity).pigCanvas()
    } else {
      PigLoading("Opening \(route.names[0])…").pigCanvas()
        .task {
          for name in route.names {
            if let found = await model.savedArtist(name: name) {
              artist = found
              break
            }
          }
          searched = true
        }
    }
  }
}
