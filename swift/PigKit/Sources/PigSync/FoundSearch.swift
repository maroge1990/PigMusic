import Foundation
import Observation
import PigDomain
import PigStore

public protocol FoundSearchSource: Sendable {
  func foundResults(query: String) async throws -> FoundResults
  func onlineResults(query: String) async throws -> OnlineResults
  func catalogueArtists(query: String, offset: Int) async throws -> CatalogueArtistPage
}
extension NativeService: FoundSearchSource {}

/// This phone's copy of the library, searched when the server can't answer.
public protocol LibrarySearchSource: Sendable {
  func searchPage(query: String, offset: Int, limit: Int) async throws -> LibrarySearchPage
}
extension LibraryStore: LibrarySearchSource {}

extension FoundResults {
  /// Results from this phone's library: everything in it is the person's.
  public init(library page: LibrarySearchPage) {
    let refs =
      page.artists.prefix(2).map { Ref(kind: "artist", id: $0.id) }
      + page.albums.prefix(3).map { Ref(kind: "album", id: $0.id) }
      + page.tracks.prefix(FoundScreen.topSize).map { Ref(kind: "song", id: $0.id) }
    self.init(
      top: Array(refs.prefix(FoundScreen.topSize)), songs: page.tracks,
      albums: page.albums.map { Marked($0, inLibrary: true) },
      artists: page.artists.map { Marked($0, inLibrary: true) })
  }
}

/// The search screen's answers (D-61): the server's ranked results first,
/// then Apple's songs and albums, MusicBrainz artists and Apple Music
/// playlists (from the phone, D-66) as they arrive. A newer search drops
/// every late answer of an older one. When the server can't answer (offline,
/// or the demo library), this phone's own library is searched instead (#177).
@available(macOS 14, *)
@MainActor @Observable public final class FoundSearch {
  public private(set) var query = ""
  public private(set) var results: FoundResults?
  public private(set) var online: OnlineResults?
  public private(set) var catalogue: [CatalogueArtist] = []
  public private(set) var applePlaylists: [ApplePlaylist] = []
  public private(set) var loading = false
  public private(set) var error: String?
  /// The results came from this phone's library, not the server.
  public private(set) var fromThisPhone = false
  public var screen: FoundScreen {
    FoundScreen(results, online: online, catalogue: catalogue, applePlaylists: applePlaylists)
  }
  @ObservationIgnored private let source: (any FoundSearchSource)?
  @ObservationIgnored private let library: (any LibrarySearchSource)?
  @ObservationIgnored private let playlistSearch: ApplePlaylistSearch?
  @ObservationIgnored private let onAuthentication: @MainActor () -> Void
  @ObservationIgnored private var generation = 0
  @ObservationIgnored private var libraryGeneration = -1
  @ObservationIgnored private var answers: [String: Answer] = [:]
  private struct Answer {
    var time: Date
    var results: FoundResults?
    var online: OnlineResults?
    var catalogue: [CatalogueArtist]?
    var applePlaylists: [ApplePlaylist]?
  }
  public static let answerLifetime: TimeInterval = 300

  /// `source` nil searches only `library` (the demo).
  public init(
    source: (any FoundSearchSource)?, library: (any LibrarySearchSource)? = nil,
    applePlaylists: ApplePlaylistSearch? = nil,
    onAuthentication: @escaping @MainActor () -> Void = {}
  ) {
    self.source = source
    self.library = library
    self.playlistSearch = applePlaylists
    self.onAuthentication = onAuthentication
  }

  public func clear() {
    generation += 1
    query = ""
    results = nil
    online = nil
    catalogue = []
    applePlaylists = []
    loading = false
    error = nil
    fromThisPhone = false
  }

  private enum Delivery: Sendable {
    case results(FoundResults)
    case online(OnlineResults)
    case catalogue([CatalogueArtist])
    case applePlaylists(ApplePlaylistAnswer)
    case library(LibrarySearchPage)
    case failure(String, any Error)
  }

  /// `force` (Refresh) asks again instead of reusing an answer.
  public func search(_ text: String, force: Bool = false, now: Date = Date()) async {
    let query = SearchText.clean(text)
    guard !query.isEmpty else { return clear() }
    generation += 1
    let stamp = generation
    self.query = query
    error = nil
    // The server's last results stay until the new ones arrive, so typing
    // never blanks the list; Apple's and MusicBrainz's belong to their words.
    online = nil
    catalogue = []
    applePlaylists = []
    let key = SearchText.key(query)
    if force { answers[key] = nil }
    if let answer = answers[key], now.timeIntervalSince(answer.time) > Self.answerLifetime {
      answers[key] = nil
    }
    let reuse = answers[key]
    if let value = reuse?.results {
      results = value
      fromThisPhone = false
    }
    if let value = reuse?.online { online = value }
    if let value = reuse?.catalogue { catalogue = value }
    if let value = reuse?.applePlaylists { applePlaylists = value }
    guard let source else {
      // The demo: only this phone's library.
      loading = true
      await searchLibrary(query, stamp: stamp)
      return
    }
    let wide = query.count >= SearchText.catalogueMinimum
    let askResults = reuse?.results == nil
    let askOnline = wide && reuse?.online == nil
    let askCatalogue = wide && reuse?.catalogue == nil
    let playlistSearch = wide && reuse?.applePlaylists == nil ? self.playlistSearch : nil
    guard askResults || askOnline || askCatalogue || playlistSearch != nil else { return }
    loading = askResults
    let library = self.library
    await withTaskGroup(of: Delivery.self) { group in
      if askResults {
        group.addTask {
          do { return .results(try await source.foundResults(query: query)) } catch {
            if let library, !Self.signedOut(error), !(error is CancellationError) {
              // Offline or an unusable answer: this phone's library instead.
              do {
                return .library(try await library.searchPage(query: query, offset: 0, limit: 100))
              } catch {}
            }
            return .failure("results", error)
          }
        }
      }
      if askOnline {
        group.addTask {
          do { return .online(try await source.onlineResults(query: query)) } catch {
            return .failure("online", error)
          }
        }
      }
      if askCatalogue {
        group.addTask {
          do {
            return .catalogue(try await source.catalogueArtists(query: query, offset: 0).artists)
          } catch { return .failure("catalogue", error) }
        }
      }
      if let playlistSearch {
        // Never fails: without Apple Music the rest of search carries on.
        group.addTask { .applePlaylists(await playlistSearch.playlists(query: query)) }
      }
      for await delivery in group {
        if case .applePlaylists(let answer) = delivery {
          DiagnosticsLog.shared.record(
            "search",
            answer.diagnostics(termLength: query.count)
              + (stamp == generation ? "" : " (a newer search is showing)"))
        }
        guard stamp == generation else { continue }
        accept(delivery, key: key, now: now)
        // Without the server, its slower lanes are not waited for (Codex).
        switch delivery {
        case .library, .failure("results", _): group.cancelAll()
        default: break
        }
      }
    }
  }

  private nonisolated static func signedOut(_ error: any Error) -> Bool {
    error as? NativeFailure == .authentication
  }

  private func searchLibrary(_ query: String, stamp: Int) async {
    guard let library else {
      loading = false
      return
    }
    let delivery: Delivery
    do {
      delivery = .library(try await library.searchPage(query: query, offset: 0, limit: 100))
    } catch { delivery = .failure("results", error) }
    guard stamp == generation else { return }
    accept(delivery, key: nil, now: Date())
  }

  /// `key` nil keeps nothing: this phone's answers are never reused.
  private func accept(_ delivery: Delivery, key: String?, now: Date) {
    var answer = key.flatMap { answers[$0] } ?? Answer(time: now)
    // Once this search shows this phone's library, the server's other lanes
    // that answer late are left out: the screen says it is this phone's music.
    if libraryGeneration == generation {
      switch delivery {
      case .online, .catalogue, .applePlaylists: return
      default: break
      }
    }
    switch delivery {
    case .results(let value):
      results = value
      fromThisPhone = false
      loading = false
      answer.results = value
    case .library(let page):
      // Never remembered: the server is asked again next time.
      results = FoundResults(library: page)
      fromThisPhone = true
      libraryGeneration = generation
      loading = false
      online = nil
      catalogue = []
      return
    case .online(let value):
      online = value
      // "busy" and "unavailable" are asked again next time.
      if value.state == "ready" { answer.online = value }
    case .catalogue(let value):
      catalogue = value
      answer.catalogue = value
    case .applePlaylists(let value):
      applePlaylists = value.playlists
      // None found, a refusal or a failure is asked again next time (#125).
      if value.keep { answer.applePlaylists = value.playlists }
    case .failure(let lane, let failure):
      if failure as? NativeFailure == .authentication {
        clear()
        onAuthentication()
        return
      }
      if lane == "results" {
        loading = false
        results = nil
        fromThisPhone = false
        error = (failure as? LocalizedError)?.errorDescription ?? "Search is unavailable."
      }
      return
    }
    guard let key else { return }
    answers[key] = answer
    if answers.count > 100 { answers.removeAll() }
  }
}
