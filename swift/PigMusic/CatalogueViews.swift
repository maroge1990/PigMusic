import PigAPI
import PigBrand
import PigDomain
import SwiftUI
import UIKit

/// Full artist page: popular songs and albums, the artist's albums, then a Full
/// discography link and settings. Answers seen before draw at once and refresh quietly.
struct CatalogueArtistView: View {
  let model: AppModel
  let artistId: String
  let name: String
  @State private var profile: CatalogueProfile?
  @State private var popularity: CataloguePopularity?
  @State private var popularSongs: [Track]
  @State private var albums: CatalogueGroups?
  @State private var error: String?
  @State private var saved: Artist?
  @State private var showingLibrary = false
  init(model: AppModel, artistId: String, name: String) {
    self.model = model
    self.artistId = artistId
    self.name = name
    let popular: CataloguePopularity? = model.peekCatalogue(key: "popular:" + artistId)
    _profile = State(initialValue: model.peekCatalogue(key: "profile:" + artistId))
    _popularity = State(initialValue: popular)
    _popularSongs = State(
      initialValue: Self.songs(popular, model.peekCatalogue(key: "popular-matches:" + artistId)))
    _albums = State(initialValue: model.peekCatalogue(key: Self.albumsKey(artistId)))
  }
  @State private var scrolledPastHero = false
  var body: some View {
    ScrollView {
      ArtistHero(model: model, artistId: artistId, name: name, subtitle: profileLine)
      VStack(alignment: .leading, spacing: 16) {
        if saved != nil {
          Button {
            showingLibrary = true
          } label: {
            Label("In your library", systemImage: "checkmark.circle.fill")
              .font(.subheadline.weight(.semibold))
          }
          .buttonStyle(ChipStyle(selected: false))
          .accessibilityHint("Opens \(name) in your library")
        }
        if let description = profile?.description, !description.isEmpty {
          Text(description).font(PigType.subhead).foregroundStyle(.secondary)
        }
        if !popularSongs.isEmpty {
          VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
              Text("Popular songs").font(.title3.weight(.bold))
              Spacer()
              PlayCapsule { model.play(popularSongs, origin: .catalogue(artistId: artistId)) }
            }.padding(.bottom, 6)
            ForEach(Array(popularSongs.prefix(10).enumerated()), id: \.element.id) {
              index, track in
              SongRow(model: model, track: track) {
                model.play(popularSongs, at: index, origin: .catalogue(artistId: artistId))
              }
              Divider()
            }
          }
        }
        if let popular = popularity, !popular.albums.isEmpty {
          // The Library's album grid and tiles, not a row of fixed cards (#62).
          Text("Popular albums").font(.title3.weight(.bold))
          LazyVGrid(columns: cardColumns, spacing: 18) {
            ForEach(popular.albums) { release in
              NavigationLink {
                CatalogueAlbumView(model: model, groupId: release.id, title: release.title)
              } label: {
                CollectionCard(
                  model: model, source: .releaseGroup(release.id), title: release.title,
                  caption: String(release.date.prefix(4)))
              }.buttonStyle(.plain)
            }
          }
        }
        if let albums, !albums.releases.isEmpty {
          Text("Albums").font(.title3.weight(.bold))
          ReleaseGrid(model: model, releases: albums.releases.sorted { $0.date > $1.date })
        } else if albums == nil {
          if let error {
            Text(error).font(PigType.footnote).foregroundStyle(.secondary)
            Button("Try again") { Task { await loadAlbums() } }
              .buttonStyle(ChipStyle(selected: false))
          } else {
            ProgressView().frame(maxWidth: .infinity)
          }
        }
        NavigationLink {
          CatalogueDiscographyView(model: model, artistId: artistId, name: name)
        } label: {
          DiscographyLinkLabel()
        }.buttonStyle(.plain)
        DetailSettings {
          NavigationLink {
            ArtistAcquisitionView(model: model, artistId: artistId)
          } label: {
            HStack {
              Label("Requests and monitoring", systemImage: "bell")
              Spacer()
              Image(systemName: "chevron.right").foregroundStyle(.tertiary)
            }
          }
        }
      }.padding(.horizontal).padding(.bottom, 24)
    }
    .ignoresSafeArea(edges: .top)
    .onScrollGeometryChange(for: Bool.self) { $0.contentOffset.y > ArtistHero.height - 120 } action: {
      _, past in
      withAnimation(.easeInOut(duration: 0.2)) { scrolledPastHero = past }
    }
    .pigCanvas().navigationTitle(scrolledPastHero ? name : "").navigationBarTitleDisplayMode(.inline)
    .toolbarBackground(scrolledPastHero ? .automatic : .hidden, for: .navigationBar)
    .navigationDestination(isPresented: $showingLibrary) {
      if let saved { SavedArtistView(model: model, artist: saved) }
    }
    .task {
      // Every part loads at once; none waits for another.
      async let profile: Void = loadProfile()
      async let popular: Void = loadPopular()
      async let albums: Void = loadAlbums()
      async let saved: Void = findSaved()
      _ = await (profile, popular, albums, saved)
    }
  }
  static func albumsKey(_ artistId: String) -> String { "groups:\(artistId):album:0" }
  private static func songs(_ popular: CataloguePopularity?, _ matches: [Track?]?) -> [Track] {
    let tracks = popular?.songs.map(\.track) ?? []
    return tracks.enumerated().map { index, track in
      matches.flatMap { $0.indices.contains(index) ? $0[index] : nil } ?? track
    }
  }
  private var profileLine: String? {
    guard let profile else { return nil }
    let line = [profile.type, profile.country, profile.begin].filter { !$0.isEmpty }
      .joined(separator: " · ")
    return line.isEmpty ? nil : line
  }
  private func loadProfile() async {
    if let value = try? await model.cachedCatalogue(
      key: "profile:" + artistId, load: { try await $0.catalogueProfile(artistId: artistId) })
    {
      profile = value
    }
  }
  private func loadPopular() async {
    // A list saved on this device draws while the server is asked, so the
    // section is there when the page opens rather than arriving later.
    if popularity == nil,
      let saved = await model.storedCatalogue(key: "popular:" + artistId, as: CataloguePopularity.self)
    {
      popularity = saved
      popularSongs = Self.songs(
        saved,
        await model.storedCatalogue(key: "popular-matches:" + artistId, as: [Track?].self))
    }
    guard
      let popular = try? await model.cachedCatalogue(
        key: "popular:" + artistId, load: { try await $0.cataloguePopularity(artistId: artistId) })
    else { return }
    popularity = popular
    // Show the songs straight away; library matches replace rows as they arrive.
    popularSongs = Self.songs(
      popular, model.peekCatalogue(key: "popular-matches:" + artistId, as: [Track?].self))
    let tracks = popular.songs.map(\.track)
    let matches =
      tracks.isEmpty
      ? nil
      : try? await model.cachedCatalogue(key: "popular-matches:" + artistId) {
        try await $0.lookupSongs(tracks)
      }
    popularSongs = Self.songs(popular, matches)
  }
  private func loadAlbums() async {
    do {
      albums = try await model.cachedCatalogue(key: Self.albumsKey(artistId)) {
        try await $0.catalogueGroups(artistId: artistId, offset: 0, kind: "album", name: name)
      }
      error = nil
    } catch is CancellationError {} catch { self.error = error.userMessage }
  }
  /// The library artist with this name, confirmed against its catalogue identity
  /// when the server can say; the identity is not required offline.
  private func findSaved() async {
    guard let candidate = await model.savedArtist(name: name) else { return }
    do {
      let identity = try await model.cachedCatalogue(key: "identity:" + candidate.id) {
        try await $0.artistIdentity(id: candidate.id)
      }
      if identity.id == artistId { saved = candidate }
    } catch is CancellationError {} catch {
      if model.service == nil { saved = candidate }
    }
  }
}

/// The full artist page's header, like the web: an edge-to-edge photograph that
/// fades and drifts as the page scrolls, with the portrait and the artist's logo
/// (or name). Without a Fanart photograph a blurred portrait fills the same space,
/// so the layout never jumps when the photograph arrives.
struct ArtistHero: View {
  static let height: CGFloat = 340
  let model: AppModel
  let artistId: String
  let name: String
  let subtitle: String?
  @State private var background: UIImage?
  @State private var portrait: UIImage?
  @State private var logo: UIImage?
  @Environment(\.colorScheme) private var scheme
  @Environment(\.accessibilityReduceMotion) private var reduceMotion
  init(model: AppModel, artistId: String, name: String, subtitle: String?) {
    self.model = model
    self.artistId = artistId
    self.name = name
    self.subtitle = subtitle
    _background = State(initialValue: model.memoryCatalogueImage("background", artistId, width: 1024))
    _portrait = State(initialValue: model.memoryCatalogueImage("artist", artistId, width: 512))
    _logo = State(initialValue: model.memoryCatalogueImage("logo", artistId, width: 1024))
  }
  var body: some View {
    GeometryReader { proxy in
      let offset = proxy.frame(in: .scrollView).minY
      let progress = min(1, max(0, -offset / (Self.height * 0.8)))
      ZStack(alignment: .bottomLeading) {
        // The fade is a mask on the photograph itself, so it drifts with the
        // photograph and the picture melts into the canvas instead of sliding
        // out from under a gradient that scrolls at a different speed.
        backdrop
          .frame(width: proxy.size.width, height: Self.height + max(0, offset))
          .clipped()
          .mask {
            LinearGradient(
              stops: [
                .init(color: .black, location: 0.4),
                .init(color: .black.opacity(0.3), location: 0.72),
                .init(color: .clear, location: 1),
              ], startPoint: .top, endPoint: .bottom)
          }
          .opacity(1 - progress * 0.9)
          .blur(radius: reduceMotion ? 0 : progress * 12)
          .offset(y: offset > 0 ? -offset : (reduceMotion ? 0 : -offset * 0.45))
        HStack(alignment: .bottom, spacing: 14) {
          Artwork(model: model, source: .artist(artistId), title: name, width: 512, circle: true)
            .frame(width: 92, height: 92)
            .shadow(color: .black.opacity(0.25), radius: 10, y: 4)
          VStack(alignment: .leading, spacing: 4) {
            Text("ARTIST").font(.caption.weight(.bold)).tracking(1.2)
              .foregroundStyle(PigBrand.textAccent(scheme))
            if let logo {
              Image(uiImage: logo).resizable().scaledToFit()
                .frame(maxWidth: 220, maxHeight: 64, alignment: .leading)
                .accessibilityLabel(name)
            } else {
              Text(name).font(.largeTitle.weight(.bold)).lineLimit(2).minimumScaleFactor(0.6)
            }
            if let subtitle {
              Text(subtitle).font(PigType.subhead).foregroundStyle(.secondary).lineLimit(1)
            }
          }
          Spacer(minLength: 0)
        }
        .padding(.horizontal).padding(.bottom, 8)
        .opacity(1 - progress)
      }
    }
    .frame(height: Self.height)
    .accessibilityElement(children: .combine)
    .accessibilityAddTraits(.isHeader)
    // Each image appears as soon as it arrives.
    .task(id: artistId) {
      if background == nil, let image = await model.artistBackground(id: artistId) {
        withAnimation(.easeIn(duration: 0.3)) { background = image }
      }
    }
    .task(id: artistId) {
      if portrait == nil, let image = await model.artistArtwork(id: artistId) { portrait = image }
    }
    .task(id: artistId) {
      if logo == nil, let image = await model.artistLogo(id: artistId) {
        withAnimation(.easeIn(duration: 0.3)) { logo = image }
      }
    }
  }
  @ViewBuilder private var backdrop: some View {
    if let background {
      Image(uiImage: background).resizable().scaledToFill()
    } else if let portrait {
      Image(uiImage: portrait).resizable().scaledToFill().blur(radius: 40).saturation(1.3)
        .opacity(0.7)
    } else {
      PigBrand.surface(scheme)
    }
  }
}

/// The bottom-of-page link to an artist's complete catalogue.
struct DiscographyLinkLabel: View {
  var title = "Full discography"
  var body: some View {
    HStack {
      Label(title, systemImage: "square.stack.3d.up")
        .font(.subheadline.weight(.semibold))
      Spacer()
      Image(systemName: "chevron.right").foregroundStyle(.tertiary)
    }
    .padding(.vertical, 12).padding(.horizontal, 14)
    .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
    .contentShape(Rectangle())
  }
}

private struct ReleaseGrid: View {
  let model: AppModel
  let releases: [CatalogueGroup]
  var showType = false
  var body: some View {
    LazyVGrid(columns: cardColumns, spacing: 18) {
      ForEach(releases, id: \.id) { release in
        NavigationLink {
          CatalogueAlbumView(
            model: model, groupId: release.id, title: release.title,
            savedAlbumId: release.savedAlbumId)
        } label: {
          CollectionCard(
            model: model, source: .releaseGroup(release.id), title: release.title,
            caption: [
              showType ? release.type : "", String(release.date.prefix(4)),
              release.savedAlbumId == nil ? "" : "In library",
            ]
            .filter { !$0.isEmpty }.joined(separator: " · "))
        }.buttonStyle(.plain)
      }
    }
  }
}

/// Every release by an artist, filtered by type.
struct CatalogueDiscographyView: View {
  let model: AppModel
  let artistId: String
  let name: String
  @State private var kind = "all"
  @State private var order = "date"
  @State private var releases: [CatalogueGroup]
  @State private var offset: Int
  @State private var total: Int
  @State private var loading = false
  @State private var loaded: Bool
  @State private var error: String?
  init(model: AppModel, artistId: String, name: String) {
    self.model = model
    self.artistId = artistId
    self.name = name
    let page: CatalogueGroups? = model.peekCatalogue(key: "groups:\(artistId):all:0")
    _releases = State(initialValue: page?.releases ?? [])
    _offset = State(initialValue: page.map { $0.offset + $0.releases.count } ?? 0)
    _total = State(initialValue: page?.total ?? 0)
    _loaded = State(initialValue: page != nil)
  }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 16) {
        HStack {
          Text(kindLabel).font(.title3.weight(.bold))
          Spacer()
          Menu {
            Picker("Release type", selection: $kind) {
              Text("All releases").tag("all")
              Text("Albums").tag("album")
              Text("Singles").tag("single")
              Text("EPs").tag("ep")
              Text("Other").tag("other")
            }
            Picker("Order", selection: $order) {
              Text("Newest first").tag("date")
              Text("Title").tag("title")
            }
          } label: {
            Label("Filter", systemImage: "line.3.horizontal.decrease.circle")
              .font(PigType.footnote)
          }
        }
        if releases.isEmpty {
          CatalogueStatus(loading: loading, loaded: loaded, empty: true, error: error) {
            Task { await load(reset: true) }
          }
        }
        ReleaseGrid(model: model, releases: orderedReleases, showType: true)
        if offset < total {
          Button("Show more releases") { Task { await load(reset: false) } }.disabled(loading)
            .buttonStyle(ChipStyle(selected: false)).frame(maxWidth: .infinity)
        }
      }.padding(.horizontal).padding(.bottom, 24)
    }
    .pigCanvas().navigationTitle(name).navigationBarTitleDisplayMode(.inline)
    .task(id: kind) { await load(reset: true) }
  }
  private var kindLabel: String {
    ["all": "All releases", "album": "Albums", "single": "Singles", "ep": "EPs", "other": "Other"][
      kind] ?? "All releases"
  }
  private var orderedReleases: [CatalogueGroup] {
    releases.sorted {
      order == "title"
        ? $0.title.localizedStandardCompare($1.title) == .orderedAscending
        : ($0.date, $0.title) > ($1.date, $1.title)
    }
  }
  private func key(_ kind: String, _ offset: Int) -> String { "groups:\(artistId):\(kind):\(offset)" }
  private func load(reset: Bool) async {
    guard reset || !loading else { return }
    let selectedKind = kind
    let next = reset ? 0 : offset
    if reset {
      // Show what this filter showed before, if anything, while it refreshes.
      let page: CatalogueGroups? = model.peekCatalogue(key: key(selectedKind, 0))
      releases = page?.releases ?? []
      offset = page.map { $0.offset + $0.releases.count } ?? 0
      total = page?.total ?? 0
      loaded = page != nil
    }
    loading = true
    error = nil
    defer { if kind == selectedKind { loading = false } }
    do {
      let page = try await model.cachedCatalogue(key: key(selectedKind, next)) {
        try await $0.catalogueGroups(
          artistId: artistId, offset: next, kind: selectedKind == "all" ? nil : selectedKind,
          name: name)
      }
      try Task.checkCancellation()
      guard kind == selectedKind else { return }
      releases = reset ? page.releases : releases + page.releases
      offset = page.offset + page.releases.count
      total = page.releases.isEmpty ? offset : page.total
      loaded = true
    } catch is CancellationError {} catch {
      if kind == selectedKind { self.error = error.userMessage }
    }
  }
}

/// An album from the catalogue opens its most likely edition straight away; the
/// other editions are offered at the bottom of that page.
struct CatalogueAlbumView: View {
  let model: AppModel
  let groupId: String
  let title: String
  var artist: String?
  /// Your library copy of this album, linked from its page.
  var savedAlbumId: String?
  @State private var editions: CatalogueEditions?
  @State private var error: String?
  init(
    model: AppModel, groupId: String, title: String, artist: String? = nil,
    savedAlbumId: String? = nil
  ) {
    self.model = model
    self.groupId = groupId
    self.title = title
    self.artist = artist
    self.savedAlbumId = savedAlbumId
    _editions = State(initialValue: model.peekCatalogue(key: "editions:\(groupId):0"))
  }
  var body: some View {
    Group {
      if let first = editions?.editions.first {
        CatalogueReleaseView(
          model: model, releaseId: first.id, title: first.title, groupId: groupId,
          editionCount: editions?.total ?? 1, savedAlbumId: savedAlbumId
        ).id(first.id)
      } else {
        // The card's cover and title straight away, as for a library album.
        List {
          Section {
            DetailHeader(
              model: model, source: .releaseGroup(groupId), eyebrow: "ALBUM", title: title,
              artist: artist)
          }.listRowSeparator(.hidden)
          if let error {
            Text(error).foregroundStyle(.secondary)
            Button("Try again") { Task { await load() } }
          } else if editions != nil {
            Text("No catalogue results.").foregroundStyle(.secondary)
          } else {
            ProgressView().frame(maxWidth: .infinity)
          }
        }.pigCanvas().navigationTitle(title).navigationBarTitleDisplayMode(.inline)
      }
    }
    .task { await load() }
  }
  private func load() async {
    error = nil
    if editions == nil {
      editions = await model.storedCatalogue(key: "editions:\(groupId):0")
    }
    do {
      // Editions arrive ranked; the first is the recommended one.
      editions = try await model.cachedCatalogue(
        key: "editions:\(groupId):0",
        conditional: { try await $0.catalogueEditions(groupId: groupId, offset: 0, etag: $1) })
    } catch is CancellationError {} catch { self.error = error.userMessage }
  }
}

struct CatalogueEditionsView: View {
  let model: AppModel
  let groupId: String
  let title: String
  @State private var editions: [CatalogueEdition] = []
  @State private var offset = 0
  @State private var total = 0
  @State private var loading = false
  @State private var loaded = false
  @State private var error: String?
  var body: some View {
    List {
      CatalogueStatus(loading: loading, loaded: loaded, empty: editions.isEmpty, error: error) {
        Task { await load(reset: !loaded) }
      }
      ForEach(Array(editions.enumerated()), id: \.offset) { _, edition in
        NavigationLink {
          CatalogueReleaseView(model: model, releaseId: edition.id, title: edition.title)
        } label: {
          HStack(spacing: 12) {
          ArtworkThumb(model: model, source: .release(edition.id), title: edition.title, size: 52)
          VStack(alignment: .leading) {
            Text(edition.title)
            Text(
              [edition.date, edition.country, edition.formats.joined(separator: ", ")]
                .filter { !$0.isEmpty }.joined(separator: " · ")
            )
            .font(PigType.caption).foregroundStyle(.secondary)
            if !edition.description.isEmpty { Text(edition.description).font(PigType.caption) }
            Text("\(edition.trackCount) songs · \(edition.status)").font(PigType.caption)
              .foregroundStyle(.secondary)
          }
          }
        }
      }
      if offset < total {
        Button("Load more editions") { Task { await load(reset: false) } }.disabled(loading)
      }
    }.pigCanvas().navigationTitle(title).task { await load(reset: true) }
  }
  private func load(reset: Bool) async {
    guard !loading else { return }
    loading = true
    error = nil
    defer { loading = false }
    let next = reset ? 0 : offset
    do {
      let page = try await model.cachedCatalogue(
        key: "editions:\(groupId):\(next)",
        conditional: { try await $0.catalogueEditions(groupId: groupId, offset: next, etag: $1) })
      editions = reset ? page.editions : editions + page.editions
      offset = page.offset + page.editions.count
      total = page.editions.isEmpty ? offset : page.total
      loaded = true
    } catch is CancellationError {} catch { self.error = error.userMessage }
  }
}

struct CatalogueReleaseView: View {
  let model: AppModel
  let releaseId: String
  let title: String
  /// Set when this edition was chosen for an album, to offer its other editions.
  let groupId: String?
  let editionCount: Int
  var savedAlbumId: String?
  @State private var release: CatalogueRelease?
  @State private var savedAlbum: Album?
  @State private var showingArtist = false
  @State private var requesting = false
  @State private var requestFailure: String?
  @State private var loading = false
  @State private var error: String?
  @Environment(\.colorScheme) private var scheme
  init(
    model: AppModel, releaseId: String, title: String, groupId: String? = nil,
    editionCount: Int = 1, savedAlbumId: String? = nil
  ) {
    self.model = model
    self.releaseId = releaseId
    self.title = title
    self.groupId = groupId
    self.editionCount = editionCount
    self.savedAlbumId = savedAlbumId
    _release = State(initialValue: model.peekCatalogue(key: "release:" + releaseId))
  }
  var body: some View {
    List {
      if release == nil {
        Section {
          DetailHeader(
            model: model, source: .release(releaseId),
            fallback: groupId.map { .releaseGroup($0) }, eyebrow: "ALBUM", title: title)
        }.listRowSeparator(.hidden)
        if let error {
          Text(error).foregroundStyle(.secondary)
          Button("Try again") { Task { await load() } }.disabled(loading)
        } else {
          ProgressView().frame(maxWidth: .infinity)
        }
      } else if release?.songs.isEmpty == true {
        Text("No catalogue results.").foregroundStyle(.secondary)
      }
      if let release {
        Section {
          DetailHeader(
            model: model, source: .release(releaseId),
            fallback: groupId.map { .releaseGroup($0) }, eyebrow: "ALBUM", title: release.title,
            artist: release.artist,
            openArtist: release.artistId == nil ? nil : { showingArtist = true }
          ).preparesPlayback(release.songs.map(\.track), model: model)
          let missing = release.songs.filter {
            $0.availability != "in_library" && $0.availability != "requested"
          }.map(\.track)
          HStack(spacing: 10) {
            PlayCapsule { model.play(release.songs.map(\.track), origin: origin) }
              .disabled(release.songs.isEmpty)
            RoundIcon(label: "Shuffle", systemImage: "shuffle") {
              model.play(release.songs.map(\.track).shuffled(), origin: origin)
            }.disabled(release.songs.isEmpty)
            // The request is shown only by its mark: sending, then the song
            // states (clock = requested), or a warning that retries.
            if requesting {
              ProgressView().frame(width: 40, height: 40).accessibilityLabel("Sending request")
            } else if let requestFailure {
              RoundIcon(label: "Request failed: \(requestFailure). Try again",
                systemImage: "exclamationmark.circle") { Task { await request(missing) } }
              .foregroundStyle(PigBrand.danger(scheme))
            } else if !missing.isEmpty {
              RoundIcon(label: "Add or request album", systemImage: "plus") {
                Task { await request(missing) }
              }
              .disabled(!model.canMutate || missing.count > 500)
            } else if release.songs.contains(where: { $0.availability == "requested" }) {
              RoundIconLabel(systemImage: "clock").accessibilityLabel("Requested")
            }
            Spacer()
          }.buttonStyle(.plain)
          if !release.songs.isEmpty
            && release.songs.allSatisfy({ $0.availability == "in_library" })
          {
            Text("Already in your library: nothing to request.")
              .font(PigType.footnote).foregroundStyle(.secondary)
          }
          if let savedAlbum {
            NavigationLink {
              AlbumView(model: model, album: savedAlbum)
            } label: {
              Label("Open in your library", systemImage: "checkmark.circle.fill")
                .font(.subheadline.weight(.semibold))
            }
          }
        }
        .listRowSeparator(.hidden)
        Section {
          ForEach(Array(release.songs.enumerated()), id: \.offset) { index, song in
            SongRow(model: model, track: track(song), number: index + 1) {
              model.play(release.songs.map(\.track), at: index, origin: origin)
            }
          }
        }
        if let groupId, editionCount > 1 {
          Section {
            NavigationLink {
              CatalogueEditionsView(model: model, groupId: groupId, title: release.title)
            } label: {
              Label(
                "Other versions of this album (\(editionCount - 1))", systemImage: "square.stack")
            }
          }
        }
      }
    }
    .pigCanvas().navigationTitle(release?.title ?? title)
    .navigationDestination(isPresented: $showingArtist) {
      if let release, let artistId = release.artistId {
        CatalogueArtistView(model: model, artistId: artistId, name: release.artist)
      }
    }
    .task { await load() }
    .task(id: savedAlbumId) {
      if let savedAlbumId { savedAlbum = try? await model.store?.album(id: savedAlbumId) }
    }
  }
  private var origin: PlayOrigin { .catalogue(artistId: release?.artistId) }
  private func request(_ tracks: [Track]) async {
    guard !requesting else { return }
    requesting = true
    requestFailure = nil
    defer { requesting = false }
    do {
      _ = try await model.performPersonal { try await $0.requestMusic(tracks) }
      await load()
    } catch is CancellationError {} catch { requestFailure = error.userMessage }
  }
  private func track(_ song: CatalogueSong) -> Track {
    var track = song.track
    track.serverState =
      switch song.availability {
      case "in_library": "in_library"
      case "household_available": "household_available"
      case "requested": "requested"
      default: "not_in_library"
      }
    return track
  }
  private func load() async {
    guard !loading else { return }
    loading = true
    error = nil
    defer { loading = false }
    if release == nil { release = await model.storedCatalogue(key: "release:" + releaseId) }
    do {
      release = try await model.cachedCatalogue(
        key: "release:" + releaseId,
        conditional: { try await $0.catalogueRelease(releaseId: releaseId, etag: $1) })
    } catch is CancellationError {} catch { self.error = error.userMessage }
  }
}

private struct CatalogueStatus: View {
  let loading: Bool
  let loaded: Bool
  let empty: Bool
  let error: String?
  let retry: () -> Void
  var body: some View {
    if loading { ProgressView("Loading catalogue…") }
    if let error {
      Text(error).foregroundStyle(.secondary)
      Button("Try again", action: retry).disabled(loading)
    } else if loaded && empty {
      Text("No catalogue results.").foregroundStyle(.secondary)
    }
  }
}
