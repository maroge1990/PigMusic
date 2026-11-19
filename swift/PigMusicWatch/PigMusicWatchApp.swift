import PigBrand
import PigCompanion
import PigDomain
import PigPlayback
import SwiftUI
import WatchKit

@main struct PigMusicWatchApp: App {
  @State private var model = WatchModel()
  @State private var starting = true
  @Environment(\.scenePhase) private var scenePhase
  var body: some Scene {
    WindowGroup {
      WatchLibraryView(model: model)
        .overlay { if starting { PigBrandSplash(size: 120) } }
        .task {
          #if DEBUG
            if WatchModel.demo { await model.loadDemo() }
          #endif
          await model.reload()
          starting = false
        }
        .onChange(of: scenePhase) { _, phase in
          model.sceneActive = phase == .active
          if phase == .active { DiagnosticsLog.shared.foreground() }
          if phase == .background {
            DiagnosticsLog.shared.background()
            // Removals waiting for their Undo are sent before the Watch sleeps.
            model.undo.sendAll()
          }
          if phase == .active { Task { await model.reload() } }
        }
    }
    .backgroundTask(.watchConnectivity) { await model.finishBackgroundDelivery() }
  }
}

extension WatchLibraryStatus.Headline {
  var symbol: String {
    switch self {
    case .ready: "checkmark.circle.fill"
    case .storageFull, .couldntSave: "exclamationmark.circle"
    case .iPhoneAway: "iphone.slash"
    case .arriving: "clock"
    }
  }
}

/// Before a run: how much music will play with no iPhone or network, or
/// what is keeping songs from the Watch (#252).
private struct WatchReadiness: View {
  let model: WatchModel
  var body: some View {
    let status = model.status
    let summary = WatchReadinessSummary(
      ready: status.ready, waiting: status.waiting, failed: status.couldntSave,
      seconds: status.readySeconds, bytes: status.readyBytes)
    VStack(alignment: .leading, spacing: 2) {
      Label(status.title, systemImage: status.headline.symbol).font(.headline)
      Text(status.count).font(PigType.footnote)
      Text(summary.listening)
        .font(PigType.footnote).foregroundStyle(.secondary)
      if let advice = status.advice {
        Text(advice).font(PigType.footnote).foregroundStyle(.secondary)
      } else if status.headline == .ready, let synced = model.lastSynced {
        Text("Refreshed \(synced.formatted(.relative(presentation: .named)))")
          .font(PigType.footnote).foregroundStyle(.secondary)
      }
    }
    .accessibilityElement(children: .combine)
  }
}

/// The small square at the start of every Watch row (D-120). Songs reach
/// the Watch without their covers, so each kind has its own tile.
private struct WatchCover: View {
  @Environment(\.colorScheme) private var scheme
  let symbol: String
  var body: some View {
    Image(systemName: symbol).font(.system(size: 14, weight: .semibold))
      .foregroundStyle(PigBrand.accent(scheme))
      .frame(width: 32, height: 32)
      .background(PigBrand.raised(scheme))
      .clipShape(RoundedRectangle(cornerRadius: 5))
    .accessibilityHidden(true)
  }
}

/// One row style for every Watch list (D-120): a cover, the name, and a
/// second line.
private struct WatchRow: View {
  let title: String
  var detail: String? = nil
  let symbol: String
  var body: some View {
    HStack(spacing: 8) {
      WatchCover(symbol: symbol)
      VStack(alignment: .leading, spacing: 1) {
        Text(title).font(.body.weight(.semibold)).lineLimit(2)
        if let detail {
          Text(detail).font(PigType.caption).foregroundStyle(.secondary).lineLimit(1)
        }
      }.frame(maxWidth: .infinity, alignment: .leading)
    }
  }
}

/// Like Apple Music and Spotify on the Watch: what is playing first (here or on
/// the iPhone), then the library by playlist, artist and album, with storage
/// and refresh last.
private struct WatchLibraryView: View {
  @Bindable var model: WatchModel
  var body: some View {
    NavigationStack {
      List {
        WatchPhoneNowPlayingRow(model: model)
        WatchNowPlayingRow(model: model)
        if let error = model.loadError {
          // Not "No music": the music is there, and couldn't be opened (#252).
          ContentUnavailableView {
            Label("Couldn't open your music", systemImage: "exclamationmark.circle")
          } description: {
            Text(error)
          } actions: {
            Button("Try again", systemImage: "arrow.clockwise") { Task { await model.reopen() } }
          }.listRowBackground(Color.clear)
        } else if model.songs.isEmpty {
          ContentUnavailableView {
            Label {
              Text("No music on Apple Watch yet")
            } icon: {
              Image("PigLockup").resizable().scaledToFit().frame(height: 32)
                .accessibilityHidden(true)
            }
          } description: {
            Text("On iPhone, use Send to Apple Watch on a playlist, artist or album.")
          }.listRowBackground(Color.clear)
        } else {
          WatchReadiness(model: model)
          let playlists = model.manifest.collections.filter { !$0.songIds.isEmpty }
          if !playlists.isEmpty {
            libraryLink("Playlists", count: playlists.count, symbol: "music.note.list") {
              List {
                ForEach(playlists) { collection in
                  let name = collection.favourites ? "Favourites" : collection.name
                  NavigationLink {
                    WatchSongs(model: model, ids: collection.songIds, title: name)
                  } label: {
                    WatchRow(
                      title: name,
                      detail: "\(collection.songIds.count) song\(collection.songIds.count == 1 ? "" : "s")",
                      symbol: collection.favourites ? "star.fill" : "music.note.list")
                  }
                }
              }.pigBrandedCanvas().navigationTitle("Playlists")
            }
          }
          libraryLink("Artists", count: model.artists.count, symbol: "music.mic") {
            List {
              ForEach(model.artists) { artist in
                NavigationLink {
                  WatchArtistView(model: model, artist: artist)
                } label: {
                  WatchRow(
                    title: artist.name,
                    detail: "\(artist.albums.count) album\(artist.albums.count == 1 ? "" : "s")",
                    symbol: "music.mic")
                }
              }
            }.pigBrandedCanvas().navigationTitle("Artists")
          }
          libraryLink("Albums", count: model.albums.count, symbol: "square.stack") {
            WatchAlbums(model: model, albums: model.albums, title: "Albums")
          }
          libraryLink("Songs", count: model.songs.count, symbol: "music.note") {
            WatchSongs(model: model, ids: model.songIds, title: "Songs")
          }
          Button {
            Task { await model.play(model.songs.map(\.id).shuffled()) }
          } label: {
            Label("Shuffle all", systemImage: "shuffle")
          }
        }
        Section("More") {
          NavigationLink("Storage") { WatchStorage(model: model) }
        }
        if model.manifest.context.account == nil, model.loadError == nil {
          Text("Sign in and open Apple Watch in PigMusic on iPhone to send music.").font(PigType.footnote)
        }
      }.pigBrandedCanvas().navigationTitle("PigMusic")
    }.pigBrandedCanvas()
  }
  private func libraryLink<Destination: View>(
    _ title: String, count: Int, symbol: String,
    @ViewBuilder destination: @escaping () -> Destination
  ) -> some View {
    NavigationLink(destination: destination) {
      Label {
        HStack {
          Text(title)
          Spacer()
          Text("\(count)").foregroundStyle(.secondary).pigFigures()
        }
      } icon: {
        Image(systemName: symbol)
      }.font(.headline).padding(.vertical, 4)
    }
  }
}

/// Storage & sync (D-120 screen 24): "1.2 GB of 2 GB" with a gauge, the
/// song count, the Storage limit beside them, and what keeps songs away.
private struct WatchStorage: View {
  @Environment(\.colorScheme) private var scheme
  @Bindable var model: WatchModel
  var body: some View {
    let status = model.status
    let figures = WatchStorageFigures(
      usedBytes: model.receipt?.usedBytes ?? 0,
      limitBytes: model.receipt?.budgetBytes ?? WatchStorageFigures.gigabyte)
    List {
      HStack(spacing: 10) {
        Gauge(value: figures.fraction) {
          EmptyView()
        } currentValueLabel: {
          Text(figures.percent).pigFigures()
        }
        .gaugeStyle(.accessoryCircularCapacity).tint(PigBrand.accent(scheme))
        VStack(alignment: .leading, spacing: 2) {
          Text(figures.used).font(.title3.weight(.bold)).pigFigures()
          Text(figures.limit).font(PigType.footnote).foregroundStyle(.secondary)
          Text("\(status.ready) song\(status.ready == 1 ? "" : "s")").font(PigType.footnote)
            .foregroundStyle(.secondary).pigFigures()
        }
      }
      .accessibilityElement(children: .ignore)
      .accessibilityLabel(
        "\(figures.used) \(figures.limit) used, \(status.ready) song\(status.ready == 1 ? "" : "s")")
      .listRowBackground(Color.clear)
      if status.headline != .ready {
        VStack(alignment: .leading, spacing: 2) {
          Label(status.title, systemImage: status.headline.symbol).font(.headline)
          Text(status.count).font(PigType.footnote)
          if let advice = status.storageAdvice {
            Text(advice).font(PigType.footnote).foregroundStyle(.secondary)
          }
        }.accessibilityElement(children: .combine)
      }
      if model.iPhoneReachable == false, status.headline != .iPhoneAway {
        Label("Can't reach your iPhone", systemImage: "iphone.slash").font(PigType.footnote)
      }
      Picker(
        "Storage limit",
        selection: Binding(
          get: { figures.limitGigabytes },
          set: { value in Task { await model.setBudget(value) } })
      ) {
        ForEach(WatchStorageFigures.limits, id: \.self) { Text("\($0) GB").tag($0) }
      }
      Button("Refresh from iPhone", systemImage: "arrow.clockwise") {
        Task { await model.send("sync") }
      }
      if let synced = model.lastSynced {
        Text("Refreshed \(synced.formatted(.relative(presentation: .named)))")
          .font(PigType.footnote).foregroundStyle(.secondary)
      }
      WatchNotice(model: model)
      if let error = model.link?.error {
        Text(error).font(PigType.footnote).foregroundStyle(.secondary)
      }
      Button("Send diagnostics to iPhone") { model.sendDiagnosticsFromStorage() }
      Toggle("Crossfade", isOn: $model.crossfade)
    }.pigBrandedCanvas().navigationTitle("Storage")
  }
}

/// A passing notice ("Sent to iPhone") in grey; it clears on its own (#252).
private struct WatchNotice: View {
  let model: WatchModel
  var body: some View {
    if let notice = model.notices.current {
      Text(notice.text).font(PigType.footnote).foregroundStyle(.secondary)
        .frame(maxWidth: .infinity).multilineTextAlignment(.center)
        .listRowBackground(Color.clear)
        .accessibilityAddTraits(.updatesFrequently)
    }
  }
}

private struct WatchAlbums: View {
  let model: WatchModel
  let albums: [WatchAlbum]
  let title: String
  var body: some View {
    List {
      ForEach(albums) { album in
        NavigationLink {
          WatchSongs(model: model, ids: album.ids, title: album.title)
        } label: {
          WatchRow(title: album.title, detail: album.artist, symbol: "square.stack")
        }
      }
    }.pigBrandedCanvas().navigationTitle(title)
  }
}
private struct WatchArtistView: View {
  let model: WatchModel
  let artist: WatchArtist
  var body: some View {
    List {
      HStack {
        Button {
          Task { await model.play(artist.ids) }
        } label: {
          Label("Play", systemImage: "play.fill")
        }.pigPrimaryAction()
        Button {
          Task { await model.play(artist.ids.shuffled()) }
        } label: {
          Image(systemName: "shuffle")
        }.accessibilityLabel("Shuffle")
      }.listRowBackground(Color.clear)
      ForEach(artist.albums) { album in
        NavigationLink {
          WatchSongs(model: model, ids: album.ids, title: album.title)
        } label: {
          WatchRow(
            title: album.title,
            detail: "\(album.ids.count) song\(album.ids.count == 1 ? "" : "s")",
            symbol: "square.stack")
        }
      }
    }.pigBrandedCanvas().navigationTitle(artist.name)
  }
}
/// What the iPhone plays while the Watch plays nothing (D-104). Apple's Now
/// Playing controls the iPhone's audio directly, with no PigMusic message.
private struct WatchPhoneNowPlayingRow: View {
  let model: WatchModel
  var body: some View {
    if let state = model.phoneNowPlaying, let title = state.title,
      PhoneNowPlaying.showsRow(state, watchPlaying: model.playback.wantsPlayback, now: Date())
    {
      let artist = state.artist ?? ""
      NavigationLink {
        NowPlayingView()
      } label: {
        HStack(spacing: 8) {
          Group {
            if let cover = model.phoneCover {
              Image(uiImage: cover).resizable().scaledToFill()
            } else {
              Image(systemName: "music.note").foregroundStyle(.secondary)
            }
          }
          .frame(width: 36, height: 36).clipShape(RoundedRectangle(cornerRadius: 6))
          VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.headline).lineLimit(1)
            Text(artist).font(PigType.caption).foregroundStyle(.secondary).lineLimit(1)
            Label("iPhone", systemImage: "iphone").font(.caption2).foregroundStyle(.secondary)
          }.frame(maxWidth: .infinity, alignment: .leading)
        }
      }
      .accessibilityElement(children: .ignore)
      .accessibilityLabel("Now playing on iPhone, \(title), \(artist)")
      .accessibilityAddTraits(.isButton)
    }
  }
}
private struct WatchNowPlayingRow: View {
  @Bindable var model: WatchModel
  var body: some View {
    if model.queue.indices.contains(model.playback.index) {
      let track = model.queue[model.playback.index].track
      HStack(spacing: 8) {
        NavigationLink {
          WatchNowPlaying(model: model)
        } label: {
          // Full width, so play/pause sits at the row's end.
          VStack(alignment: .leading, spacing: 2) {
            // "Paused" when it is (#255: it said Now playing while paused).
            Text(model.playback.wantsPlayback ? "Now playing" : "Paused")
              .font(.caption2).foregroundStyle(.secondary)
            Text(track.title).font(.headline).lineLimit(1)
            Text(track.artist).font(PigType.caption).foregroundStyle(.secondary).lineLimit(1)
          }.frame(maxWidth: .infinity, alignment: .leading)
        }
        // The accent button, not .plain: plain kept the on-accent icon colour
        // with no accent behind it, which hid it on the dark canvas. Round and
        // its own size, since Watch buttons otherwise share the row's width.
        Button {
          model.toggle()
        } label: {
          Image(systemName: model.playback.wantsPlayback ? "pause.fill" : "play.fill")
            .frame(width: 28, height: 28)
        }
        .pigPrimaryAction().buttonBorderShape(.circle).fixedSize()
        .accessibilityLabel(model.playback.wantsPlayback ? "Pause" : "Play")
      }
    }
  }
}
private struct WatchSongs: View {
  let model: WatchModel
  let all: [String]
  let title: String
  init(model: WatchModel, ids: [String], title: String) {
    self.model = model
    self.all = ids
    self.title = title
  }
  /// Without songs waiting for their removal's Undo (D-79, #183), or on
  /// their way out.
  private var ids: [String] {
    all.filter { !model.undo.hides(WatchModel.undoKey($0)) && !model.removing.contains($0) }
  }
  var body: some View {
    List {
      // The latest removal, with its Undo, until it is sent.
      if let item = model.undo.items.last {
        Button {
          model.undo.undo(item.id)
        } label: {
          VStack(alignment: .leading) {
            Label("Undo", systemImage: "arrow.uturn.backward").font(PigType.headline)
            Text(item.label).font(PigType.caption).foregroundStyle(.secondary).lineLimit(2)
          }
        }
        .accessibilityLabel("Undo: " + item.label)
      }
      HStack {
        Button {
          Task { await model.play(ids) }
        } label: {
          Label("Play", systemImage: "play.fill")
        }.pigPrimaryAction()
        Button {
          Task { await model.play(ids.shuffled()) }
        } label: {
          Image(systemName: "shuffle")
        }.accessibilityLabel("Shuffle")
      }.disabled(ids.isEmpty).listRowBackground(Color.clear)
      // One tap plays from this song; swipe to remove its download.
      ForEach(Array(ids.enumerated()), id: \.offset) { index, id in
        if let song = model.manifest.songs.first(where: { $0.id == id }) {
          let ready = model.isReady(song)
          Button {
            Task { await model.play(ids, at: index) }
          } label: {
            WatchRow(
              title: song.track.title, detail: ready ? song.track.artist : waiting(id),
              symbol: "music.note")
          }
          .disabled(!ready)
          .swipeActions {
            // At once, with Undo (D-79): sent when the Undo runs out.
            Button("Remove", systemImage: "minus.circle", role: .destructive) {
              model.undo.schedule(WatchModel.undoKey(id), label: "Removed " + song.track.title) {
                await model.send("remove", songIds: [id])
                return true
              }
            }
          }
        }
      }
    }.pigBrandedCanvas().navigationTitle(title)
      // A tap when a song leaves with its Undo (#183).
      .sensoryFeedback(trigger: model.undo.items.count) { old, new in
        new > old ? .impact(weight: .light) : nil
      }
  }
  /// Why a song isn't on the Watch yet, in the row's second line.
  private func waiting(_ id: String) -> String {
    switch model.problems[id] {
    case .storageFull: "Storage full"
    case .unreadable: "Couldn't save on Apple Watch"
    case nil: "Waiting for Apple Watch"
    }
  }
}
/// The system Now Playing first: large and glanceable, with Digital Crown
/// volume and the audio output picker (AirPods, Bluetooth). It drives PigMusic
/// through the remote commands. Seeking, repeat and errors are one swipe away.
private struct WatchNowPlaying: View {
  @Bindable var model: WatchModel
  var body: some View {
    TabView {
      // No title: the system player puts the clock where it would go.
      NowPlayingView()
      WatchPlaybackDetails(model: model)
    }.tabViewStyle(.verticalPage)
  }
}
/// Controls (D-120 screen 25): the position with its times, a labelled
/// Volume, Repeat, and notices in grey. Try again only after a playback error.
private struct WatchPlaybackDetails: View {
  @Environment(\.colorScheme) private var scheme
  @Bindable var model: WatchModel
  var body: some View {
    List {
      HStack {
        Button {
          model.previous()
        } label: {
          Image(systemName: "backward.fill").frame(minWidth: 40, minHeight: 44)
        }.accessibilityLabel("Previous song")
        Button {
          model.toggle()
        } label: {
          Image(systemName: model.playback.wantsPlayback ? "pause.fill" : "play.fill")
            .frame(minWidth: 40, minHeight: 44)
        }.pigPrimaryAction().accessibilityLabel(model.playback.wantsPlayback ? "Pause" : "Play")
        Button {
          model.next()
        } label: {
          Image(systemName: "forward.fill").frame(minWidth: 40, minHeight: 44)
        }.accessibilityLabel("Next song")
      }.buttonStyle(.plain).padding(8).pigFloatingControls().listRowBackground(Color.clear)
      VStack(alignment: .leading, spacing: 3) {
        if model.queue.indices.contains(model.playback.index) {
          Text(model.queue[model.playback.index].track.title).font(PigType.caption)
            .foregroundStyle(.secondary).lineLimit(1)
        }
        Slider(
          value: Binding(
            get: { min(model.playback.duration, model.playback.elapsed) }, set: { model.seek($0) }),
          in: 0...max(1, model.playback.duration)
        ).accessibilityLabel("Playback position")
        HStack {
          Text(WatchClock.elapsed(model.playback.elapsed))
          Spacer()
          Text(
            WatchClock.remaining(elapsed: model.playback.elapsed, duration: model.playback.duration))
        }.font(.caption2).foregroundStyle(.secondary).pigFigures()
      }.listRowBackground(Color.clear)
      VStack(alignment: .leading, spacing: 3) {
        Text("Volume").font(PigType.caption).foregroundStyle(.secondary)
        Slider(value: $model.volume, in: 0...1).accessibilityLabel("Volume")
      }.listRowBackground(Color.clear)
      Picker("Repeat", selection: $model.repeatMode) {
        Text("Off").tag(PlaybackRepeat.off)
        Text("All").tag(PlaybackRepeat.all)
        Text("One").tag(PlaybackRepeat.one)
      }
      if let error = model.playback.error {
        Text(error).font(PigType.footnote).foregroundStyle(PigBrand.danger(scheme))
        Button("Try again", systemImage: "arrow.clockwise") { Task { await model.retry() } }
      }
      WatchNotice(model: model)
    }.pigBrandedCanvas().navigationTitle("Controls")
  }
}
