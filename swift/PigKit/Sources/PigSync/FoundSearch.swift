import Foundation
import Observation
import PigDomain

public protocol FoundSearchSource: Sendable {
  func foundResults(query: String) async throws -> FoundResults
  func onlineResults(query: String) async throws -> OnlineResults
  func catalogueArtists(query: String, offset: Int) async throws -> CatalogueArtistPage
}
extension NativeService: FoundSearchSource {}

/// The search screen's answers (D-61): the server's ranked results first,
/// then Apple's songs and albums and MusicBrainz artists as they arrive. A
/// newer search drops every late answer of an older one.
@available(macOS 14, *)
@MainActor @Observable public final class FoundSearch {
  public private(set) var query = ""
  public private(set) var results: FoundResults?
  public private(set) var online: OnlineResults?
  public private(set) var catalogue: [CatalogueArtist] = []
  public private(set) var loading = false
  public private(set) var error: String?
  /// The server predates this search: the earlier one is used instead.
  public private(set) var unsupported = false
  public var screen: FoundScreen { FoundScreen(results, online: online, catalogue: catalogue) }
  @ObservationIgnored private let source: any FoundSearchSource
  @ObservationIgnored private let onAuthentication: @MainActor () -> Void
  @ObservationIgnored private var generation = 0
  @ObservationIgnored private var answers: [String: Answer] = [:]
  private struct Answer {
    var time: Date
    var results: FoundResults?
    var online: OnlineResults?
    var catalogue: [CatalogueArtist]?
  }
  public static let answerLifetime: TimeInterval = 300

  public init(
    source: any FoundSearchSource, onAuthentication: @escaping @MainActor () -> Void = {}
  ) {
    self.source = source
    self.onAuthentication = onAuthentication
  }

  public func clear() {
    generation += 1
    query = ""
    results = nil
    online = nil
    catalogue = []
    loading = false
    error = nil
  }

  private enum Delivery: Sendable {
    case results(FoundResults)
    case online(OnlineResults)
    case catalogue([CatalogueArtist])
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
    let key = SearchText.key(query)
    if force { answers[key] = nil }
    if let answer = answers[key], now.timeIntervalSince(answer.time) > Self.answerLifetime {
      answers[key] = nil
    }
    let reuse = answers[key]
    if let value = reuse?.results { results = value }
    if let value = reuse?.online { online = value }
    if let value = reuse?.catalogue { catalogue = value }
    let wide = query.count >= SearchText.catalogueMinimum
    let askResults = reuse?.results == nil
    let askOnline = wide && reuse?.online == nil
    let askCatalogue = wide && reuse?.catalogue == nil
    guard askResults || askOnline || askCatalogue else { return }
    loading = askResults
    let source = self.source
    await withTaskGroup(of: Delivery.self) { group in
      if askResults {
        group.addTask {
          do { return .results(try await source.foundResults(query: query)) } catch {
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
      for await delivery in group {
        guard stamp == generation else { continue }
        accept(delivery, key: key, now: now)
        // Without the ranked results the earlier search takes over: it must
        // not wait for Apple's or MusicBrainz's slower answers (Codex).
        if case .failure("results", _) = delivery { group.cancelAll() }
      }
    }
  }

  private func accept(_ delivery: Delivery, key: String, now: Date) {
    var answer = answers[key] ?? Answer(time: now)
    switch delivery {
    case .results(let value):
      results = value
      loading = false
      answer.results = value
    case .online(let value):
      online = value
      // "busy" and "unavailable" are asked again next time.
      if value.state == "ready" { answer.online = value }
    case .catalogue(let value):
      catalogue = value
      answer.catalogue = value
    case .failure(let lane, let failure):
      if failure is SearchUnsupported {
        unsupported = true
        loading = false
        return
      }
      if failure as? NativeFailure == .authentication {
        clear()
        onAuthentication()
        return
      }
      if lane == "results" {
        loading = false
        results = nil
        error = (failure as? LocalizedError)?.errorDescription ?? "Search is unavailable."
      }
      return
    }
    answers[key] = answer
    if answers.count > 100 { answers.removeAll() }
  }
}
