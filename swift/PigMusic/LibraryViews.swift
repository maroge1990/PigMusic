import PigAPI
import AVKit
import PigBrand
import PigDomain
import PigPlayback
import SwiftUI

private enum LibraryTab: String, CaseIterable, Identifiable {
  case artists = "Artists"
  case albums = "Albums"
  case musicals = "Musicals"
  case soundtracks = "Soundtracks"
  case compilations = "Compilations"
  case songs = "Songs"
  case playlists = "Playlists"
  var id: String { rawValue }
  /// Album tabs show one category each, as on the web.
  var albumCategory: String? {
    switch self {
    case .albums: "album"
    case .musicals: "musical"
    case .soundtracks: "soundtrack"
    case .compilations: "compilation"
    default: nil
    }
  }
}

/// "Your library": collection chips and a card grid, as in the web client.
struct LibraryView: View {
  @Bindable var model: AppModel
  @State private var tab: LibraryTab = .artists
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 16, pinnedViews: []) {
        VStack(alignment: .leading, spacing: 2) {
          Text(
            [model.person?.displayName, "\(model.songCount.formatted()) songs"].compactMap { $0 }
              .joined(separator: " · ")
          ).font(PigType.subhead).foregroundStyle(.secondary)
          if let message = model.message {
            Text(message).font(PigType.footnote).foregroundStyle(.secondary)
          }
        }
        HStack(spacing: 8) {
          ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
              // Empty category tabs stay hidden, as on the web.
              ForEach(
                LibraryTab.allCases.filter {
                  $0 == .albums || $0.albumCategory.map { model.albumCategoryCounts[$0, default: 0] > 0 } ?? true
                }
              ) { item in
                Button(item.rawValue) {
                  tab = item
                  if let category = item.albumCategory {
                    Task { await model.showAlbums(category: category) }
                  }
                }
                  .buttonStyle(ChipStyle(selected: tab == item))
                  .accessibilityAddTraits(tab == item ? .isSelected : [])
              }
            }
          }
        }
        content
        if model.songCount == 0 {
          ContentUnavailableView(
            "Your library", systemImage: "music.note",
            description: Text(
              model.syncing
                ? "Syncing your music…"
                : "Search for songs and albums to add, or pull to refresh."))
        }
      }
      .padding(.horizontal).padding(.bottom, 24)
    }
    .navigationTitle("Your library")
    .refreshable { await model.refresh() }
    #if DEBUG
      // Headless UI review: --open-album shows the first album page.
      .navigationDestination(item: $debugAlbum) { AlbumView(model: model, album: $0) }
      .task {
        guard ProcessInfo.processInfo.arguments.contains("--open-album") else { return }
        while model.albums.isEmpty { try? await Task.sleep(for: .milliseconds(200)) }
        debugAlbum = model.albums.first
      }
    #endif
  }
  #if DEBUG
    @State private var debugAlbum: Album?
  #endif
  @ViewBuilder private var content: some View {
    switch tab {
    case .artists:
      sort(.artists, selected: model.artistSort, artistOption: false)
      LazyVGrid(columns: cardColumns, spacing: 18) {
        ForEach(model.artists) { artist in
          NavigationLink {
            SavedArtistView(model: model, artist: artist)
          } label: {
            SavedArtistCard(model: model, artist: artist)
          }.buttonStyle(.plain)
        }
      }
      more(.artists, shown: model.artists.count, total: model.artistCount)
    case .albums, .musicals, .soundtracks, .compilations:
      sort(.albums, selected: model.albumSort)
      LazyVGrid(columns: cardColumns, spacing: 18) {
        ForEach(model.albums) { album in
          NavigationLink {
            AlbumView(model: model, album: album)
          } label: {
            CollectionCard(
              model: model, source: album.artworkSource, title: album.title, caption: album.artist)
          }.buttonStyle(.plain)
        }
      }
      more(.albums, shown: model.albums.count, total: model.albumCount)
    case .songs:
      sort(.tracks, selected: model.songSort)
      LazyVStack(spacing: 0) {
        ForEach(Array(model.songs.enumerated()), id: \.element.id) { index, track in
          SongRow(model: model, track: track) { model.play(model.songs, at: index) }
          Divider()
        }
      }
      more(.tracks, shown: model.songs.count, total: model.songCount)
    case .playlists:
      NavigationLink {
        PlaylistToolsView(model: model)
      } label: {
        Label("New or imported playlist", systemImage: "plus.circle")
      }.padding(.vertical, 4)
      LazyVStack(spacing: 0) {
        ForEach(model.playlists) { playlist in
          NavigationLink {
            PlaylistView(model: model, playlist: playlist)
          } label: {
            HStack(spacing: 12) {
              Image(systemName: playlist.favourites == true ? "heart.fill" : "music.note.list")
                .frame(width: 44, height: 44)
                .foregroundStyle(PigBrand.accent(scheme))
                .background(PigBrand.accent(scheme).opacity(0.12), in: .rect(cornerRadius: 10))
              VStack(alignment: .leading) {
                Text(playlist.name).foregroundStyle(PigBrand.text(scheme))
                if let count = playlist.trackCount ?? playlist.entries?.count {
                  Text("\(count) song\(count == 1 ? "" : "s")").font(PigType.footnote)
                    .foregroundStyle(.secondary)
                }
              }
              Spacer()
              Image(systemName: "chevron.right").foregroundStyle(.tertiary)
            }.frame(minHeight: 56).contentShape(Rectangle())
          }.buttonStyle(.plain)
          Divider()
        }
      }
    }
  }
  private func sort(
    _ collection: LibraryCollection, selected: LibrarySort, artistOption: Bool = true
  )
    -> some View
  {
    LibrarySortPicker(
      model: model, collection: collection, selected: selected, artist: artistOption)
  }
  @ViewBuilder private func more(_ collection: LibraryCollection, shown: Int, total: Int)
    -> some View
  {
    if shown < total {
      Button("Show more") { Task { await model.more(collection) } }
        .buttonStyle(ChipStyle(selected: false))
        .disabled(model.loadingMore || model.syncing)
        .frame(maxWidth: .infinity)
        // Grids page in as they scroll into view.
        .onAppear { Task { await model.more(collection) } }
    }
  }
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
  @State private var tracks: [Track] = []
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
    return merged + owned
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
      VStack(alignment: .leading, spacing: 14) {
        DetailHeader(
          model: model, source: album.artworkSource ?? tracks.first?.artworkSource,
          eyebrow: "IN YOUR LIBRARY", title: album.title, subtitle: albumSubtitle,
          artist: album.artist, openArtist: artist == nil ? nil : { showingArtist = true }
        ).preparesPlayback(tracks, model: model)
        HStack(spacing: 10) {
          PlayCapsule { model.play(tracks) }.disabled(tracks.isEmpty)
          RoundIcon(label: "Shuffle", systemImage: "shuffle") { model.play(tracks.shuffled()) }
            .disabled(tracks.isEmpty)
          DownloadMenu(model: model, tracks: tracks)
          Spacer()
        }
        LazyVStack(spacing: 0) {
          let rows = rows
          ForEach(Array(rows.enumerated()), id: \.offset) { index, song in
            SongRow(model: model, track: song, number: song.trackNumber ?? index + 1) {
              model.play(rows, at: index)
            }
            Divider()
          }
        }
        if !missing.isEmpty {
          VStack(alignment: .leading, spacing: 6) {
            Button {
              Task { await requestMissing() }
            } label: {
              Label(
                "Request \(missing.count) missing song\(missing.count == 1 ? "" : "s")",
                systemImage: "arrow.down.circle")
            }
            .buttonStyle(ChipStyle(selected: false))
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
      tracks = await model.albumTracks(album)
      artist = await model.savedArtist(id: album.artistId, name: album.artist)
      await loadRelease()
    }
  }
  private func loadRelease() async {
    guard
      let releaseId = album.musicbrainzReleaseId
        ?? tracks.lazy.compactMap(\.musicbrainzReleaseId).first
    else { return }
    // Offline, the last answer is used; without one the page shows what you own.
    release = try? await model.cachedCatalogue(
      key: "release:" + releaseId,
      conditional: { try await $0.catalogueRelease(releaseId: releaseId, etag: $1) })
  }
  private func requestMissing() async {
    guard !requesting else { return }
    requesting = true
    defer { requesting = false }
    do {
      let result = try await model.performPersonal { try await $0.requestMusic(missing) }
      requestMessage = "Requested \(result.created) song\(result.created == 1 ? "" : "s")."
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

/// Device and Watch download choices behind one round button.
struct DownloadMenu: View {
  let model: AppModel
  let tracks: [Track]
  /// A playlist's downloads are grouped under it on the device.
  var origin: DownloadOrigin = .direct
  /// Songs not in your library yet; offered first, as a server request.
  var missingCount = 0
  var addMissing: (() async -> Void)?
  @State private var watchResult: String?
  var body: some View {
    Menu {
      if missingCount > 0, let addMissing {
        Section {
          Button(
            "Add \(missingCount) missing song\(missingCount == 1 ? "" : "s") to server",
            systemImage: "externaldrive.badge.plus"
          ) {
            Task { await addMissing() }
          }.disabled(!model.canMutate)
        }
      }
      Button("Download originals", systemImage: "arrow.down.circle") {
        Task { await model.download(tracks, origin: origin) }
      }
      Button("Download smaller AAC copies", systemImage: "arrow.down.circle.dotted") {
        Task { await model.download(tracks, quality: "aac", origin: origin) }
      }
      if model.watch?.supported == true {
        Button("Send to Apple Watch", systemImage: "applewatch") {
          Task { watchResult = await model.watch?.enqueue(tracks, origin: origin) }
        }
      }
    } label: {
      RoundIconLabel(systemImage: "arrow.down")
    }
    // An empty playlist can still be sent: the Watch follows it as songs are added.
    .disabled(tracks.isEmpty && origin.playlistId == nil).accessibilityLabel("Download")
    .alert(
      "Apple Watch",
      isPresented: Binding(get: { watchResult != nil }, set: { if !$0 { watchResult = nil } })
    ) {
      Button("OK") { watchResult = nil }
    } message: {
      Text(watchResult ?? "")
    }
  }
}

/// Kept for screens that list songs inside a `List`.
struct SongButton: View {
  let model: AppModel
  let track: Track
  let play: () -> Void
  var body: some View { SongRow(model: model, track: track, play: play) }
}

struct AccountView: View {
  @Bindable var model: AppModel
  var body: some View {
    Form {
      Section {
        VStack(alignment: .leading, spacing: 2) {
          Text(model.person?.displayName ?? "").font(.headline)
          Text(model.server).font(PigType.footnote).foregroundStyle(.secondary)
            .textSelection(.enabled)
        }
        NavigationLink("Appearance") { PreferencesView(model: model) }
        NavigationLink("Password") { PasswordView(model: model) }
        NavigationLink("Devices and sessions") { SessionsView(model: model) }
        NavigationLink("Server quality") { QualityView(model: model) }
        NavigationLink("Pending edits (\(model.pendingEdits.count))") {
          PendingEditsView(model: model)
        }
        NavigationLink("Diagnostics") { DiagnosticsView(model: model) }
      }
      Section("Library") {
        LabeledContent("Songs on the server", value: model.songCount.formatted())
        Button("Sync now") { Task { await model.refresh() } }.disabled(model.syncing)
      }
      Section {
        Toggle(
          "Seamless playback",
          isOn: Binding(
            get: { model.player.seamlessEnabled }, set: { model.player.seamlessEnabled = $0 }))
        if model.player.seamlessEnabled {
          Toggle(
            "Crossfade",
            isOn: Binding(
              get: { model.player.crossfadeEnabled }, set: { model.player.crossfadeEnabled = $0 }))
        }
      } header: {
        Text("Playback")
      } footer: {
        Text(
          "Seamless playback gives gapless albums and a three-second crossfade between unrelated songs. A song that is very large or slow to arrive plays as a normal stream automatically."
        )
      }
      Section {
        Button("Sign out", role: .destructive) { Task { await model.signOut() } }.disabled(
          model.busy || model.mutationActive)
      }
      if let message = model.message { Section { Text(message).font(PigType.footnote) } }
    }.navigationTitle("Account")
  }
}

/// Integrated mini player. On iOS 26.1+ it is the tab bar's accessory, which the
/// system shows inline beside a minimized tab bar.
struct MiniPlayer: View {
  @Bindable var model: AppModel
  private var player: PlayerController { model.player }
  let open: () -> Void
  @Environment(\.colorScheme) private var scheme
  var body: some View {
    HStack(spacing: 10) {
      Button(action: open) {
        HStack(spacing: 10) {
          ArtworkThumb(
            model: model, source: player.queue.current?.track.artworkSource,
            title: player.queue.current?.track.title ?? "", size: 32)
          VStack(alignment: .leading, spacing: 0) {
            Text(player.queue.current?.track.title ?? "").font(.subheadline.weight(.semibold))
              .lineLimit(1)
            Text(status).font(.caption).foregroundStyle(.secondary).lineLimit(1)
          }
          Spacer(minLength: 0)
        }.contentShape(Rectangle())
      }
      .buttonStyle(.plain)
      .accessibilityLabel("Now playing: \(player.queue.current?.track.title ?? "")")
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
  @Bindable var model: AppModel
  /// Opens the playing song's artist where playback started (library or catalogue).
  var openArtist: (ArtistRoute) -> Void = { _ in }
  private var player: PlayerController { model.player }
  @Environment(\.dismiss) private var dismiss
  @Environment(\.colorScheme) private var scheme
  /// Dragging updates only this value; one seek happens when the drag ends.
  @State private var scrubbing: Double?
  // Sections are separate properties so each type-checks quickly.
  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(spacing: 20) {
          artwork
          titles
          progress
          transport
          // No status words in normal use (#60): a spinner while preparing.
          if player.preparing { ProgressView().controlSize(.small) }
          options
          if let error = player.error { failure(error) }
          upNext
        }.padding(.horizontal, 24).padding(.bottom, 24)
      }
      .navigationTitle("Now playing").navigationBarTitleDisplayMode(.inline)
      .toolbar { Button("Done") { dismiss() } }
    }.pigBrandedCanvas()
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
      Text(current?.title ?? "").font(.title2.weight(.bold)).multilineTextAlignment(.center)
      Button {
        if let current {
          openArtist(ArtistRoute(track: current, origin: model.playOrigin))
          dismiss()
        }
      } label: {
        Text(current?.artist ?? "").font(.headline)
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
      }.font(.caption.monospacedDigit()).foregroundStyle(.secondary)
    }
  }
  private var transport: some View {
    HStack(spacing: 40) {
      Button(action: player.previous) {
        Image(systemName: "backward.fill").font(.title).frame(minWidth: 44, minHeight: 44)
      }.accessibilityLabel("Previous song")
      Button(action: player.toggle) {
        Image(systemName: player.wantsPlayback ? "pause.circle.fill" : "play.circle.fill")
          .font(.system(size: 64)).frame(minWidth: 64, minHeight: 64)
      }.accessibilityLabel(player.wantsPlayback ? "Pause" : "Play")
      Button(action: player.next) {
        Image(systemName: "forward.fill").font(.title).frame(minWidth: 44, minHeight: 44)
      }.accessibilityLabel("Next song")
    }.buttonStyle(.plain)
  }
  private var options: some View {
    HStack(spacing: 12) {
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
      if let track = current {
        RoundIcon(label: "Song actions", systemImage: "ellipsis") { model.actionTrack = track }
      }
      AirPlayButton().frame(width: 40, height: 40)
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
      Text(error).font(PigType.footnote).foregroundStyle(PigBrand.danger(scheme))
        .multilineTextAlignment(.center)
      HStack {
        Button("Retry") { player.retry() }
        Button("Skip song") { player.skip() }
        if player.seamlessEnabled {
          Button("Use streaming") { player.retry(streaming: true) }
        }
      }.buttonStyle(.bordered)
    }
  }
  private var upNext: some View {
    VStack(alignment: .leading, spacing: 0) {
      HStack {
        Text("Up next").font(.headline)
        Spacer()
        NavigationLink("Edit") { QueueEditorView(player: player) }
      }.padding(.bottom, 6)
      ForEach(player.queue.items.dropFirst(player.queue.index + 1).prefix(50)) { item in
        UpNextRow(model: model, item: item)
        Divider()
      }
    }
  }
  private func time(_ seconds: Double) -> String {
    guard seconds.isFinite else { return "0:00" }
    let value = Int(seconds.rounded(.down))
    return String(format: "%d:%02d", value / 60, value % 60)
  }
}

private struct UpNextRow: View {
  let model: AppModel
  let item: PlaybackOccurrence
  var body: some View {
    HStack(spacing: 12) {
      ArtworkThumb(
        model: model, source: item.track.artworkSource, title: item.track.title, size: 40)
      VStack(alignment: .leading) {
        Text(item.track.title).lineLimit(1)
        Text(item.track.artist).font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
      }
      Spacer()
      Menu {
        Button("Move earlier") { model.player.moveUpcoming(item.id, by: -1) }
        Button("Move later") { model.player.moveUpcoming(item.id, by: 1) }
        Button("Remove from queue", role: .destructive) { model.player.removeUpcoming(item.id) }
      } label: {
        Image(systemName: "ellipsis").frame(width: 32, height: 44)
      }.accessibilityLabel("Queue actions for \(item.track.title)")
    }
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

struct QueueEditorView: View {
  @Bindable var player: PlayerController
  var body: some View {
    List {
      if let current = player.queue.current {
        Section("Current song") { Text(current.track.title) }
      }
      Section("Up next") {
        ForEach(player.queue.items.dropFirst(player.queue.index + 1)) { item in
          HStack {
            VStack(alignment: .leading) {
              Text(item.track.title)
              Text(item.track.artist).font(.caption)
            }
            Spacer()
            Menu("Queue actions", systemImage: "ellipsis") {
              Button("Move earlier") { player.moveUpcoming(item.id, by: -1) }
              Button("Move later") { player.moveUpcoming(item.id, by: 1) }
              Button("Remove", role: .destructive) { player.removeUpcoming(item.id) }
            }.labelStyle(.iconOnly)
          }
        }
      }
    }.pigCanvas().navigationTitle("Playback queue")
  }
}

private struct LibrarySortPicker: View {
  let model: AppModel
  let collection: LibraryCollection
  let selected: LibrarySort
  var artist = true
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
        Text("Title A–Z").tag(LibrarySort.title)
        Text("Title Z–A").tag(LibrarySort.titleDescending)
        if artist { Text("Artist, then title").tag(LibrarySort.artist) }
      }
    } label: {
      Label(label, systemImage: "arrow.up.arrow.down").font(PigType.footnote)
    }
    .disabled(model.loadingMore || model.syncing)
    .accessibilityLabel("Sort, \(label)")
  }
  private var label: String {
    switch selected {
    case .title: "Title A–Z"
    case .titleDescending: "Title Z–A"
    case .artist: "Artist, then title"
    }
  }
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
      ContentUnavailableView(
        route.names[0], systemImage: "music.mic",
        description: Text("This artist isn't in your library yet. Search to find them."))
    } else {
      ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity).pigCanvas()
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
