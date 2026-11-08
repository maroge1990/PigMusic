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
          if phase == .background { DiagnosticsLog.shared.background() }
          if phase == .active { Task { await model.reload() } }
        }
    }
    .backgroundTask(.watchConnectivity) { await model.finishBackgroundDelivery() }
  }
}
/// Before a run: how much music will play with no phone or network.
private struct WatchReadiness: View {
  let model: WatchModel
  var body: some View {
    let ready = model.readySongs
    let total = model.manifest.songs.count
    let summary = WatchReadinessSummary(
      ready: ready.count, waiting: total - ready.count, failed: 0,
      seconds: ready.reduce(0) { $0 + ($1.track.duration ?? 0) },
      bytes: ready.reduce(Int64(0)) { $0 + $1.bytes })
    let complete = summary.complete && model.receipt?.manifestApplied == true
    VStack(alignment: .leading, spacing: 2) {
      Label(
        complete ? "Ready for your run" : "Almost ready",
        systemImage: complete ? "checkmark.circle.fill" : "hourglass"
      ).font(.headline)
      Text(complete ? "\(total) songs" : "\(ready.count) of \(total) songs on Watch")
        .font(.footnote)
      Text(summary.listening)
        .font(.footnote).foregroundStyle(.secondary)
      if !complete {
        Text("Keep iPhone nearby").font(.footnote).foregroundStyle(.secondary)
      } else if let synced = model.lastSynced {
        Text("Synced \(synced.formatted(.relative(presentation: .named)))")
          .font(.footnote).foregroundStyle(.secondary)
      }
    }
    .accessibilityElement(children: .combine)
  }
}

/// Like Apple Music and Spotify on the Watch: what is playing first, then the
/// library by playlist, artist and album, with phone tools and storage last.
private struct WatchLibraryView: View {
  @Environment(\.colorScheme) private var scheme
  @Bindable var model: WatchModel
  var body: some View {
    NavigationStack {
      List {
        WatchNowPlayingRow(model: model)
        if !model.manifest.songs.isEmpty { WatchReadiness(model: model) }
        if model.manifest.songs.isEmpty {
          ContentUnavailableView {
            Label {
              Text("No music on your Watch yet")
            } icon: {
              Image("PigLockup").resizable().scaledToFit().frame(height: 32)
                .accessibilityHidden(true)
            }
          } description: {
            Text("On iPhone, use Send to Apple Watch on a playlist, artist or album.")
          }.listRowBackground(Color.clear)
        } else {
          let playlists = model.manifest.collections.filter { !$0.songIds.isEmpty }
          if !playlists.isEmpty {
            libraryLink("Playlists", count: playlists.count, symbol: "music.note.list") {
              List {
                ForEach(playlists) { collection in
                  NavigationLink {
                    WatchSongs(
                      model: model, ids: collection.songIds,
                      title: collection.favourites ? "Favourites" : collection.name)
                  } label: {
                    Label(
                      collection.favourites ? "Favourites" : collection.name,
                      systemImage: collection.favourites ? "star.fill" : "music.note.list")
                  }
                }
              }.pigBrandedCanvas().navigationTitle("Playlists")
            }
          }
          libraryLink("Artists", count: model.artists.count, symbol: "music.mic") {
            List {
              ForEach(model.artists) { artist in
                NavigationLink {
                  WatchArtist(model: model, artist: artist)
                } label: {
                  VStack(alignment: .leading) {
                    Text(artist.name).lineLimit(2)
                    Text("\(artist.albums.count) album\(artist.albums.count == 1 ? "" : "s")")
                      .font(.caption).foregroundStyle(.secondary)
                  }
                }
              }
            }.pigBrandedCanvas().navigationTitle("Artists")
          }
          libraryLink("Albums", count: model.albums.count, symbol: "square.stack") {
            WatchAlbums(model: model, albums: model.albums, title: "Albums")
          }
          libraryLink("Songs", count: model.manifest.songs.count, symbol: "music.note") {
            WatchSongs(model: model, ids: model.manifest.songs.map(\.id), title: "Songs")
          }
          Button {
            Task { await model.play(model.manifest.songs.map(\.id).shuffled()) }
          } label: {
            Label("Shuffle all", systemImage: "shuffle")
          }
        }
        Section("More") {
          NavigationLink("Phone remote") {
            List {
              Button("Previous", systemImage: "backward.fill") {
                Task { await model.send("previous") }
              }
              Button("Play or pause", systemImage: "playpause.fill") {
                Task { await model.send("playPause") }
              }
              Button("Next", systemImage: "forward.fill") { Task { await model.send("next") } }
            }.pigBrandedCanvas().navigationTitle("Phone remote")
          }
          NavigationLink("Storage & sync") {
            List {
              Text("\(model.receipt?.ready.count ?? 0) songs downloaded").fontDesign(.rounded)
                .monospacedDigit()
              Text(
                ByteCountFormatter.string(
                  fromByteCount: model.receipt?.usedBytes ?? 0, countStyle: .file))
              Text(
                model.link?.reachable == true
                  ? "Phone reachable" : "Phone unavailable · offline music still plays")
              Picker(
                "Watch budget",
                selection: Binding(
                  get: { Int((model.receipt?.budgetBytes ?? 1_073_741_824) / 1_073_741_824) },
                  set: { value in Task { await model.setBudget(value) } })
              ) {
                ForEach([1, 2, 4, 8], id: \.self) { Text("\($0) GB").tag($0) }
              }
              Button("Sync with phone") { Task { await model.send("sync") } }
              Button("Send diagnostics to iPhone") { model.sendDiagnostics() }
              Toggle("Crossfade", isOn: $model.crossfade)
              // The phone's words (#163). The Watch plays from its own files,
              // so albums are always gapless and there is no Seamless switch.
              Text(
                "Albums play gapless. Crossfade adds a three-second crossfade between unrelated songs. Changes apply to the next queue."
              ).font(.footnote)
              if let message = model.message ?? model.link?.error {
                Text(message).font(.footnote).foregroundStyle(PigBrand.secondary(scheme))
              }
            }.pigBrandedCanvas().navigationTitle("Storage & sync")
          }
        }
        if model.manifest.context.account == nil {
          Text("Sign in and open Apple Watch in the phone app to send music.").font(.footnote)
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
          Text("\(count)").foregroundStyle(.secondary).monospacedDigit()
        }
      } icon: {
        Image(systemName: symbol)
      }.font(.headline).padding(.vertical, 4)
    }
  }
}
private struct WatchAlbums: View {
  let model: WatchModel
  let albums: [WatchModel.AlbumGroup]
  let title: String
  var body: some View {
    List {
      ForEach(albums) { album in
        NavigationLink {
          WatchSongs(model: model, ids: album.ids, title: album.title)
        } label: {
          VStack(alignment: .leading) {
            Text(album.title).lineLimit(2)
            Text(album.artist).font(.caption).foregroundStyle(.secondary).lineLimit(1)
          }
        }
      }
    }.pigBrandedCanvas().navigationTitle(title)
  }
}
private struct WatchArtist: View {
  let model: WatchModel
  let artist: WatchModel.ArtistGroup
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
          Text(album.title).lineLimit(2)
        }
      }
    }.pigBrandedCanvas().navigationTitle(artist.name)
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
            Text("Now playing").font(.caption2).foregroundStyle(.secondary)
            Text(track.title).font(.headline).lineLimit(1)
            Text(track.artist).font(.caption).foregroundStyle(.secondary).lineLimit(1)
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
  let ids: [String]
  let title: String
  var body: some View {
    List {
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
          let ready = model.receipt?.ready[id] != nil
          Button {
            Task { await model.play(ids, at: index) }
          } label: {
            VStack(alignment: .leading) {
              Text(song.track.title).lineLimit(2)
              Text(ready ? song.track.artist : "Waiting for transfer").font(.caption)
                .foregroundStyle(.secondary).lineLimit(1)
            }
          }
          .disabled(!ready)
          .swipeActions {
            Button("Remove", role: .destructive) {
              Task { await model.send("remove", songIds: [id]) }
            }
          }
        }
      }
    }.pigBrandedCanvas().navigationTitle(title)
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
private struct WatchPlaybackDetails: View {
  @Environment(\.colorScheme) private var scheme
  @Bindable var model: WatchModel
  var body: some View {
    List {
      if model.queue.indices.contains(model.playback.index) {
        Text(model.queue[model.playback.index].track.title).font(.headline)
        Text(model.queue[model.playback.index].track.artist).font(.caption)
          .foregroundStyle(.secondary)
      }
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
      Text("\(clock(model.playback.elapsed)) / \(clock(model.playback.duration))").font(.caption)
        .fontDesign(.rounded).monospacedDigit()
      Slider(
        value: Binding(
          get: { min(model.playback.duration, model.playback.elapsed) }, set: { model.seek($0) }),
        in: 0...max(1, model.playback.duration)
      ).accessibilityLabel("Playback position")
      Picker("Repeat", selection: $model.repeatMode) {
        Text("Off").tag(PlaybackRepeat.off)
        Text("All").tag(PlaybackRepeat.all)
        Text("One").tag(PlaybackRepeat.one)
      }
      Slider(value: $model.volume, in: 0...1).accessibilityLabel("PigMusic level")
      if let error = model.playback.error ?? model.message {
        Text(error).font(.footnote).foregroundStyle(PigBrand.danger(scheme))
        Button("Retry playback") { Task { await model.retry() } }
      }
    }.pigBrandedCanvas().navigationTitle("Controls")
  }
  private func clock(_ seconds: Double) -> String {
    guard seconds.isFinite, seconds > 0 else { return "0:00" }
    let value = Int(seconds)
    return String(format: "%d:%02d", value / 60, value % 60)
  }
}
