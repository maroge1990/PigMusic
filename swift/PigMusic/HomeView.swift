import PigAPI
import PigBrand
import PigDomain
import SwiftUI

/// Home, as on the web: recently added albums, recommendations (artists and
/// albums only) and the popular chart, each as a shelf of artwork cards.
struct HomeView: View {
  @Bindable var model: AppModel
  /// The mix whose songs a long press asked to see (#56).
  @State private var openedMix: String?
  @State private var mixNotice: String?
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 28) {
        Text("Find your next favourite.").font(PigType.subhead).foregroundStyle(.secondary)
          .padding(.horizontal)
        let mixes = model.homeContent[.mixes]?.mixes ?? []
        let radio = model.homeContent[.mixes]?.radio
        if !mixes.isEmpty || radio != nil {
          Shelf(title: "Your mixes", caption: "Familiar favourites. Something new. Made for today.")
          {
            if let radio {
              Button {
                Task { await model.startRadio() }
              } label: {
                VStack(alignment: .leading, spacing: 6) {
                  MixArtwork(kind: "radio").frame(width: 150, height: 150)
                  Text(radio.title).font(PigType.cardTitle).lineLimit(2)
                  Label("Endless radio from your library", systemImage: "dot.radiowaves.left.and.right")
                    .font(PigType.caption).foregroundStyle(.secondary).lineLimit(2)
                }.frame(width: 150, alignment: .leading)
              }.buttonStyle(.plain)
            }
            ForEach(mixes) { mix in
              // Tap plays the mix; a long press offers its songs and options (#56).
              Button {
                model.play(mix.tracks)
              } label: {
                VStack(alignment: .leading, spacing: 6) {
                  MixArtwork(kind: mix.kind, title: mix.title).frame(width: 150, height: 150)
                  Text(mix.title).font(PigType.cardTitle).lineLimit(2)
                  Text(
                    (["\(mix.tracks.count) \(mix.tracks.count == 1 ? "song" : "songs")"]
                      + Array(Set(mix.tracks.map(\.artist))).sorted().prefix(3))
                      .joined(separator: " · ")
                  )
                  .font(PigType.caption).foregroundStyle(.secondary).lineLimit(2)
                }.frame(width: 150, alignment: .leading)
              }
              .buttonStyle(.plain)
              .disabled(mix.tracks.isEmpty)
              .accessibilityLabel("Play \(mix.title)")
              .accessibilityHint("Touch and hold for the songs and more options")
              .accessibilityAction(named: "Show songs") { openedMix = mix.id }
              .contextMenu { mixOptions(mix) }
            }
          }
        } else {
          VStack(alignment: .leading, spacing: 6) {
            Text("Your mixes").font(PigType.sectionTitle)
            if let error = model.homeErrors[.mixes] {
              StatusText(error, kind: .error)
            } else {
              StatusText(
                model.homeContent[.mixes]?.mixesPreparing == true
                  ? "Preparing your mixes. They'll appear once their audio is ready."
                  : "Your mixes appear as your library grows. Discoveries are prepared ahead of playback."
              )
            }
          }.padding(.horizontal)
        }
        if let recent = model.homeContent[.recent]?.albums, !recent.isEmpty {
          Shelf(title: "Recently added") {
            ForEach(recent) { album in
              NavigationLink {
                AlbumView(model: model, album: album)
              } label: {
                CollectionCard(
                  model: model, source: album.artworkSource, title: album.title,
                  caption: album.artist
                ).frame(width: 150)
              }.buttonStyle(.plain)
            }
          }
        }
        discovery(.recommended, title: "Recommended for you", songs: false)
        discovery(.popular, title: "Popular", songs: true)
      }.padding(.vertical, 8)
    }
    .navigationTitle("Home")
    .navigationDestination(item: $openedMix) { id in
      if let mix = model.homeContent[.mixes]?.mixes?.first(where: { $0.id == id }) {
        MixDetailView(model: model, mix: mix)
      }
    }
    .alert(
      "Mix",
      isPresented: Binding(get: { mixNotice != nil }, set: { if !$0 { mixNotice = nil } })
    ) {
      Button("OK") { mixNotice = nil }
    } message: {
      Text(mixNotice ?? "")
    }
    .task(id: model.artworkNamespace) { await model.refreshHome() }
    .refreshable {
      await model.refresh()
      await model.refreshHome(force: true)
    }
  }

  @ViewBuilder private func mixOptions(_ mix: PersonalMix) -> some View {
    Button("Show songs", systemImage: "list.bullet") { openedMix = mix.id }
    Button("Shuffle", systemImage: "shuffle") { model.play(mix.tracks.shuffled()) }
    Button("Save this edition", systemImage: "plus") {
      Task {
        do {
          _ = try await model.performPersonal { try await $0.saveMix(mix.id) }
          mixNotice = MixDetailView.savedNotice
        } catch { mixNotice = error.userMessage }
      }
    }.disabled(!model.canMutate)
    Button("Hide this edition", systemImage: "eye.slash", role: .destructive) {
      Task {
        do { try await model.hideMixEdition(mix.id) } catch { mixNotice = error.userMessage }
      }
    }.disabled(!model.canMutate)
  }

  @ViewBuilder private func discovery(_ section: HomeSection, title: String, songs: Bool)
    -> some View
  {
    let feed = model.homeContent[section]?.feed
    let heading = feed?.title ?? title
    VStack(alignment: .leading, spacing: 18) {
      if let feed, !feed.artists.isEmpty {
        Shelf(title: heading, caption: feed.source.isEmpty ? nil : feed.source) {
          ForEach(feed.artists) { item in
            NavigationLink {
              CatalogueArtistView(model: model, artistId: item.id, name: item.name)
            } label: {
              CollectionCard(
                model: model, source: .artist(item.id), title: item.name,
                caption: item.reason, circle: true
              ).frame(width: 140)
            }.buttonStyle(.plain)
          }
        }
      }
      if let feed, !feed.albums.isEmpty {
        Shelf(title: feed.artists.isEmpty ? heading : "Albums") {
          ForEach(feed.albums) { item in
            NavigationLink {
              CatalogueAlbumView(
                model: model, groupId: item.id, title: item.name, artist: item.artist)
            } label: {
              CollectionCard(
                model: model, source: .releaseGroup(item.id), title: item.name,
                caption: item.artist
              ).frame(width: 150)
            }.buttonStyle(.plain)
          }
        }
      }
      if songs, let feed, !feed.songs.isEmpty {
        VStack(alignment: .leading, spacing: 4) {
          Text("Songs").font(PigType.sectionTitle)
          let all = feed.songs.compactMap(\.playbackTrack)
          let tracks = Array(all.prefix(10))
          // Packed like an album's songs, without the stack's gap around
          // each divider (#126).
          VStack(spacing: 0) {
            ForEach(Array(tracks.enumerated()), id: \.offset) { index, track in
              SongRow(model: model, track: track) { model.play(all, at: index) }
              RowSeparator()
            }
          }
          // The chart holds up to 100 songs (#55); the rest are a tap away.
          if all.count > tracks.count {
            NavigationLink("See all \(all.count) songs") {
              ChartSongsView(model: model, title: heading, tracks: all)
            }.padding(.vertical, 8)
          }
        }.padding(.horizontal)
      }
      status(section, feed: feed, heading: heading)
    }
  }

  @ViewBuilder private func status(_ section: HomeSection, feed: DiscoveryFeed?, heading: String)
    -> some View
  {
    let empty = feed.map { $0.artists.isEmpty && $0.albums.isEmpty && $0.songs.isEmpty } ?? true
    if empty || model.homeErrors[section] != nil {
      VStack(alignment: .leading, spacing: 6) {
        Text(heading).font(PigType.sectionTitle)
        if model.homeLoading.contains(section) {
          ProgressView("Finding music…")
        } else if let error = model.homeErrors[section] {
          StatusText(error, kind: .error)
          Button("Try again") { Task { await model.refreshHome(force: true) } }
            .buttonStyle(.pigSecondary)
        } else if let feed {
          Text(message(section, feed)).font(PigType.footnote).foregroundStyle(.secondary)
        }
      }.padding(.horizontal)
    }
  }

  private func message(_ section: HomeSection, _ feed: DiscoveryFeed) -> String {
    if feed.state == .preparing { return "Finding what’s popular…" }
    if feed.state == .noSeeds {
      return "Recommendations appear once PigMusic has matched artists in your library."
    }
    if feed.unavailable || feed.state == .unavailable {
      return section == .popular
        ? "Charts are temporarily unavailable." : "Recommendations are temporarily unavailable."
    }
    return "Nothing here yet. Try again later."
  }
}

struct MixDetailView: View {
  static let savedNotice =
    "Playlist saved. Add songs to your library to keep their audio after this mix leaves rotation."
  @Environment(\.dismiss) private var dismiss
  let model: AppModel
  let mix: PersonalMix
  @State private var notice: StatusMessage?
  @State private var saving = false
  /// Without songs hidden, or waiting for their Undo (#183).
  private var tracks: [Track] {
    mix.tracks.filter {
      let key = AppModel.undoKey(mix: mix.id, song: $0.id)
      return !model.hiddenMixSongs.contains(key) && !model.undo.hides(key)
    }
  }
  var body: some View {
    // A List (not the album pages' ScrollView) keeps swipe to hide; styled
    // plain, with the shared header and action row, it reads as one (#158).
    List {
      Section {
        DetailHeader(
          model: model, source: nil, eyebrow: "MIX", title: mix.title,
          subtitle: [mix.reason, mix.day].filter { !$0.isEmpty }.joined(separator: " · "),
          art: AnyView(MixArtwork(kind: mix.kind, title: mix.title)))
        HStack(spacing: 10) {
          PlayCapsule { model.play(tracks) }.disabled(tracks.isEmpty)
          RoundIcon(label: "Shuffle", systemImage: "shuffle") { model.play(tracks.shuffled()) }
            .disabled(tracks.isEmpty)
          RoundIcon(label: "Save this edition", systemImage: "plus") {
            saving = true
            Task {
              do {
                _ = try await model.performPersonal { try await $0.saveMix(mix.id) }
                notice = .info(Self.savedNotice)
              } catch { notice = .error(error.userMessage) }
              saving = false
            }
          }.disabled(saving)
          Spacer()
        }.buttonStyle(.plain)
        if let notice { StatusText(notice) }
      }.listRowSeparator(.hidden).listRowBackground(Color.clear)
      Section {
        ForEach(Array(tracks.enumerated()), id: \.element.id) { index, track in
          SongRow(model: model, track: track.actionTrack) { model.play(tracks, at: index) }
            .swipeActions {
              // At once, with Undo (D-79): sent when the Undo runs out.
              Button("Hide", role: .destructive) { model.hideLater(track, from: mix) }
            }
        }
      }
    }.listStyle(.plain).pigCanvas().navigationTitle(mix.title).navigationBarTitleDisplayMode(.inline)
      .undoBar(model.undo)
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Menu {
            Button("Hide this edition", role: .destructive) {
              saving = true
              Task {
                do {
                  try await model.hideMixEdition(mix.id)
                  dismiss()
                } catch { notice = .error(error.userMessage) }
                saving = false
              }
            }.disabled(saving)
          } label: {
            Image(systemName: "ellipsis.circle").accessibilityLabel("Mix options")
          }
        }
      }
  }
}

/// Vector covers use the same motifs and Pig family colours as the web.
struct MixArtwork: View {
  let kind: String
  var title = ""
  /// "Your 1990s" → "90s"; 2000 onwards keeps the century: "2010s".
  static func decadeLabel(_ title: String) -> String {
    guard let match = title.firstMatch(of: /(\d{2})(\d)0s/) else { return "" }
    return match.1 == "19" ? "\(match.2)0s" : "\(match.1)\(match.2)0s"
  }
  var body: some View {
    Canvas { context, size in
      context.scaleBy(x: size.width / 200, y: size.height / 200)
      let pair = PigMixArtwork.colours(kind)
      let ink = GraphicsContext.Shading.color(pair.foreground)
      context.fill(Path(CGRect(x: 0, y: 0, width: 200, height: 200)), with: .color(pair.background))
      if kind == "daily" {
        for rect in [
          CGRect(x: 34, y: 47, width: 86, height: 86), CGRect(x: 80, y: 73, width: 86, height: 86),
        ] {
          context.stroke(Path(ellipseIn: rect), with: ink, lineWidth: 12)
        }
      } else if kind == "artist" {
        context.stroke(
          Path(ellipseIn: CGRect(x: 43, y: 43, width: 114, height: 114)), with: ink, lineWidth: 12)
        var orbit = context
        orbit.translateBy(x: 100, y: 100)
        orbit.rotate(by: .degrees(-35))
        orbit.stroke(
          Path(ellipseIn: CGRect(x: -83, y: -26, width: 166, height: 52)), with: ink, lineWidth: 12)
        context.fill(Path(ellipseIn: CGRect(x: 87, y: 87, width: 26, height: 26)), with: ink)
      } else if kind == "genre" {
        for i in 0..<4 {
          let y = CGFloat(65 + i * 26)
          var wave = Path()
          wave.move(to: CGPoint(x: -10, y: y))
          wave.addQuadCurve(to: CGPoint(x: 100, y: y), control: CGPoint(x: 40, y: y - 60))
          wave.addQuadCurve(to: CGPoint(x: 210, y: y), control: CGPoint(x: 160, y: y + 60))
          context.stroke(wave, with: ink, lineWidth: 12)
        }
      } else if kind == "popular" {
        for i in 0..<4 {
          context.fill(
            Path(CGRect(x: 35 + i * 36, y: 122 - i * 24, width: 20, height: 45 + i * 24)), with: ink
          )
        }
      } else if kind == "radio" {
        context.fill(Path(ellipseIn: CGRect(x: 44, y: 132, width: 24, height: 24)), with: ink)
        for r in [CGFloat(45), 80, 115] {
          var wave = Path()
          wave.addArc(
            center: CGPoint(x: 56, y: 144), radius: r, startAngle: .degrees(-90),
            endAngle: .degrees(0), clockwise: false)
          context.stroke(wave, with: ink, lineWidth: 12)
        }
      } else if kind == "fresh" {
        for angle in [0.0, 45, 90, 135] {
          var ray = context
          ray.translateBy(x: 100, y: 100)
          ray.rotate(by: .degrees(angle))
          var line = Path()
          line.move(to: CGPoint(x: 0, y: -62))
          line.addLine(to: CGPoint(x: 0, y: 62))
          ray.stroke(line, with: ink, lineWidth: 12)
        }
      } else if kind == "releases" {
        context.stroke(
          Path(ellipseIn: CGRect(x: 38, y: 38, width: 124, height: 124)), with: ink, lineWidth: 12)
        context.stroke(
          Path(ellipseIn: CGRect(x: 66, y: 66, width: 68, height: 68)), with: ink, lineWidth: 12)
        context.fill(Path(ellipseIn: CGRect(x: 91, y: 91, width: 18, height: 18)), with: ink)
        context.fill(Path(ellipseIn: CGRect(x: 146, y: 28, width: 28, height: 28)), with: ink)
      } else if kind == "decade" {
        var sun = Path()
        sun.addArc(
          center: CGPoint(x: 100, y: 84), radius: 60, startAngle: .degrees(180),
          endAngle: .degrees(0), clockwise: false)
        context.stroke(sun, with: ink, lineWidth: 12)
        var base = Path()
        base.move(to: CGPoint(x: 30, y: 168))
        base.addLine(to: CGPoint(x: 170, y: 168))
        context.stroke(base, with: ink, lineWidth: 12)
        let label = Self.decadeLabel(title)
        context.draw(
          Text(label.isEmpty ? "—" : label)
            .font(.system(size: label.count > 3 ? 50 : 64, weight: .heavy))
            .foregroundStyle(pair.foreground),
          at: CGPoint(x: 100, y: 128))
      } else if kind == "deep" {
        for (inset, width) in [(CGFloat(38), CGFloat(124)), (68, 64)] {
          context.stroke(
            Path(CGRect(x: inset, y: inset, width: width, height: width)), with: ink,
            lineWidth: 12)
        }
        context.fill(Path(CGRect(x: 92, y: 92, width: 16, height: 16)), with: ink)
      } else if kind == "recent" {
        context.stroke(Path(CGRect(x: 40, y: 70, width: 70, height: 70)), with: ink, lineWidth: 12)
        context.fill(Path(CGRect(x: 90, y: 50, width: 70, height: 70)), with: .color(pair.background))
        context.stroke(Path(CGRect(x: 90, y: 50, width: 70, height: 70)), with: ink, lineWidth: 12)
        var plus = Path()
        plus.move(to: CGPoint(x: 125, y: 68))
        plus.addLine(to: CGPoint(x: 125, y: 102))
        plus.move(to: CGPoint(x: 108, y: 85))
        plus.addLine(to: CGPoint(x: 142, y: 85))
        context.stroke(plus, with: ink, lineWidth: 12)
      } else {
        for x in [CGFloat(95), CGFloat(156)] {
          var arrow = Path()
          arrow.move(to: CGPoint(x: x, y: 57))
          arrow.addLine(to: CGPoint(x: x - 53, y: 100))
          arrow.addLine(to: CGPoint(x: x, y: 143))
          arrow.closeSubpath()
          context.fill(arrow, with: ink)
        }
      }
      var line = Path()
      line.move(to: CGPoint(x: 18, y: 182))
      line.addLine(to: CGPoint(x: 182, y: 182))
      context.stroke(line, with: ink, lineWidth: 2)
      context.fill(Path(ellipseIn: CGRect(x: 176, y: 14, width: 10, height: 10)), with: ink)
    }.aspectRatio(1, contentMode: .fit).clipShape(RoundedRectangle(cornerRadius: PigRadius.artwork))
      .accessibilityHidden(true)
  }
}

/// Every song in the popular chart, in chart order (#55), in the album
/// page's compact rows rather than taller list cells (#126).
private struct ChartSongsView: View {
  let model: AppModel
  let title: String
  let tracks: [Track]
  var body: some View {
    ScrollView {
      AlbumSongList(model: model, tracks: tracks, number: { index, _ in index + 1 }) {
        model.play(tracks, at: $0)
      }.padding(.horizontal)
    }
    .pigCanvas().navigationTitle(title).navigationBarTitleDisplayMode(.inline)
    .toolbar {
      Button("Play", systemImage: "play.fill") { model.play(tracks) }
    }
  }
}

extension AppModel {
  /// Hides a mix edition on the server and drops it from Home at once.
  func hideMixEdition(_ id: String) async throws {
    try await performPersonal { try await $0.hideMix(id) }
    let current = homeContent[.mixes]
    let updated = HomeContent(
      mixes: current?.mixes?.filter { $0.id != id },
      mixesPreparing: current?.mixesPreparing, radio: current?.radio)
    homeContent[.mixes] = updated
    try? await store?.cache(updated, key: "home:mixes")
  }
}
