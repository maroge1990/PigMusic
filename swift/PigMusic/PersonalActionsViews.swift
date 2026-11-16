import PigAPI
import PigBrand
import PigDomain
import SwiftUI

struct SongActionsView: View {
  let model: AppModel
  /// The song as the sheet opened with it; `song` is it now (#211).
  let opened: Track
  @Environment(\.dismiss) private var dismiss
  @State private var addedToLibrary = false
  /// The library's own copy of the song, read again on each sync (#211):
  /// found by id, recording, or title and artist, since a row from Home,
  /// a catalogue page or search carries a catalogue or recording id.
  @State private var own: Track?
  /// The library has been looked at once: until then the sheet offers
  /// neither Request nor the library's actions, so Request never flashes.
  @State private var looked = false
  init(model: AppModel, track: Track) {
    self.model = model
    opened = track
  }
  /// Follows search's newest answer and the library while the sheet is open,
  /// so a request that finishes offers downloads without reopening it. A
  /// song the library holds is acted on as the library's own song.
  private var track: Track {
    if var own {
      own.serverState = "in_library"
      return own
    }
    return model.liveState(opened)
  }
  private var favourite: Bool? { model.favourites[track.id] }
  @State private var loading = false
  @State private var message: StatusMessage?
  var body: some View {
    NavigationStack {
      Form {
        Section {
          VStack(alignment: .leading, spacing: 2) {
            Text(track.title).lineLimit(1)
            Text(track.artist).font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
          }
        }
        Section {
          if let favourite {
            Button(
              FavouriteStates.action(favourite: favourite),
              systemImage: FavouriteStates.symbol(favourite: favourite)
            ) {
              Task { await changeFavourite() }
            }
          } else {
            Button("Load favourite status") { Task { await loadFavourite() } }.disabled(loading)
          }
          if !looked {
            EmptyView()
          } else if addedToLibrary {
            Button("Play song", systemImage: "play.fill") {
              var saved = track
              saved.serverState = "in_library"
              model.play([saved])
              dismiss()
            }
          } else if track.serverState == "household_available" {
            Button("Add to my library", systemImage: "plus") {
              Task { await add() }
            }.disabled(loading || !model.canMutate)
          } else if track.serverState != "in_library",
            track.serverState == "requested" || model.isRequested(track.id)
          {
            // #206: the same mark as an album page's requested songs.
            Label("Requested", systemImage: "clock").foregroundStyle(.secondary)
          } else if track.serverState == "not_in_library" {
            if track.id.hasPrefix("mix:") {
              Button("Add to my library", systemImage: "arrow.down.circle") {
                Task { await request() }
              }.disabled(loading || !model.canMutate)
            } else {
              // At once, with Undo; sent when the server can be reached (#206).
              Button("Request song", systemImage: "arrow.down.circle") {
                model.requestLater(track)
              }.disabled(!model.canRequest)
            }
          }
        }
        Section {
          if model.player.queue.current != nil {
            Button("Play next") {
              model.player.enqueue([track], next: true)
              model.confirmations += 1
            }
            Button("Add to queue") {
              model.player.enqueue([track])
              model.confirmations += 1
            }
          }
          NavigationLink("Add to playlist") { AddToPlaylistView(model: model, track: track) }
          if looked, track.serverState == "in_library" || track.serverState == nil {
            Button("Download original", systemImage: "arrow.down.circle") {
              Task { await model.download([track]) }
            }
            Button("Send to Apple Watch", systemImage: "applewatch") {
              // D-103: a success tap, no text; a failure says why.
              Task {
                message = nil
                switch await model.watch?.enqueue([track]) {
                case .sent: model.confirmations += 1
                case .failed(let reason): message = .error(reason)
                case .superseded, nil: break
                }
              }
            }
            Button("Download smaller AAC copy") {
              Task { await model.download([track], quality: "aac") }
            }
            // At once, with Undo (D-79, #214).
            Button("Remove from library", systemImage: "minus.circle", role: .destructive) {
              model.removeFromLibraryLater(track)
              dismiss()
            }
          }
        }
        if loading { ProgressView() }
        if let message { Section { StatusText(message) } }
      }.pigCanvas().navigationTitle("Song actions").navigationBarTitleDisplayMode(.inline)
        .toolbar { Button("Done") { dismiss() }.disabled(loading) }
        // Again once the library's own copy is found (#211).
        .task(id: track.id) { await loadFavourite() }
        .task(id: model.libraryRevision) {
          own = await model.ownCopy(of: opened)
          looked = true
        }
    }
    .interactiveDismissDisabled(model.mutationActive)
  }
  private func loadFavourite() async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    await model.loadFavourite(track)
  }
  /// The button changes at once (#212); the message follows the save.
  private func changeFavourite() async {
    message = nil
    do {
      guard let saved = try await model.tapFavourite(track) else { return }
      message = .info(
        saved.confirmed
          ? (saved.value ? "Added to favourites." : "Removed from favourites.")
          : "Favourite saved on this iPhone; it will be sent when you're online.")
    } catch is CancellationError {} catch { message = .error(error.userMessage) }
  }
  private func add() async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    do {
      let count = try await model.performPersonal {
        try await $0.addMembership(trackIds: [track.id])
      }
      message = .info(count > 0 ? "Added to your library." : "Already in your library.")
      addedToLibrary = true
    } catch is CancellationError {} catch { message = .error(error.userMessage) }
  }
  private func request() async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    do {
      let result = try await model.performPersonal { try await $0.requestMusic([track]) }
      message = .info(
        "Requested: \(result.created) new, \(result.existing) already asked for, \(result.skipped) left out."
          + (result.downloadsEnabled ? "" : " Downloads are off on the server, so it will wait."))
    } catch is CancellationError {} catch { message = .error(error.userMessage) }
  }
}

/// Server requests, one per album request (a lone song alone), unfinished
/// first, under Transfers as on the iPhone and Watch pages (#209): swipe a
/// song to cancel it with Undo; no explanatory text (D-101). Opening a
/// request shows its songs with their source and quality.
struct RequestsView: View {
  let model: AppModel
  @State private var groups: [RequestGroup] = []
  @State private var next: Int?
  @State private var loading = false
  @State private var loaded = false
  @State private var enabled = true
  @State private var error: String?
  /// A success to report, apart from errors (Codex round 2 on #159).
  @State private var notice: String?
  var body: some View {
    List {
      if !enabled {
        StatusText("Server downloads are disabled.", kind: .error)
      }
      if let error { StatusText(error, kind: .error) }
      if let notice { StatusText(notice) }
      if loaded && groups.isEmpty { Text("No music requests").foregroundStyle(.secondary) }
      if !groups.isEmpty {
        Section("Transfers") {
          ForEach(groups) { group in
            DisclosureGroup {
              ForEach(
                group.items.filter { !model.undo.hides(AppModel.undoKey(cancelRequest: $0.id)) }
              ) { item in
                songRow(item)
                  .swipeActions {
                    if Self.cancellable.contains(item.status) {
                      Button("Cancel", role: .destructive) { cancel(item) }
                    }
                  }
              }
              let failed = group.items.filter { ["failed", "cancelled"].contains($0.status) }
              if failed.count > 1 {
                Button("Retry \(failed.count) songs") { Task { await retry(failed) } }
                  .disabled(loading || !model.canMutate)
              }
            } label: {
              VStack(alignment: .leading, spacing: 4) {
                CollectionRow(group.title, subtitle: group.artist + " · " + group.summary) {
                  ArtworkThumb(
                    model: model, source: group.releaseId.map { .release($0) }, title: group.title)
                }
                if group.state != "done" {
                  ProgressView(value: min(1, max(0, group.progress)))
                }
              }
              .accessibilityElement(children: .combine)
            }
          }
          if next != nil {
            Button("Show more requests") { Task { await load(reset: false) } }.disabled(loading)
              .buttonStyle(.pigSecondary)
          }
        }
      }
    }.pigCanvas().navigationTitle("Server").navigationBarTitleDisplayMode(.inline)
      .toolbar {
        Button("Clear finished") { Task { await clear() } }
          .disabled(loading || !model.canMutate)
      }
      .task { await load(reset: true) }
      // Live: the server's request queue changed.
      .onChange(of: model.requestsRevision) { Task { await load(reset: true) } }
  }
  private static let cancellable: Set<String> = [
    "waiting_for_sources", "searching", "downloading", "validating",
  ]
  private func songRow(_ item: RequestGroupItem) -> some View {
    VStack(alignment: .leading, spacing: 4) {
      Text(item.title)
      Text(
        [
          RequestStatus.label(item.status), item.source.map(RequestSource.name) ?? "",
          item.quality ?? "",
        ]
        .filter { !$0.isEmpty }.joined(separator: " · ")
      ).font(PigType.caption).foregroundStyle(.secondary)
      if let progress = item.requestProgress {
        ProgressView(value: min(1, max(0, progress)))
      }
      if let error = item.error, !error.isEmpty {
        StatusText(error, kind: .error)
      }
      if ["failed", "cancelled"].contains(item.status) {
        Button("Retry request") { Task { await retry([item]) } }
          .disabled(loading || !model.canMutate)
      }
    }
  }
  private func retry(_ items: [RequestGroupItem]) async {
    do {
      for item in items {
        _ = try await model.performPersonal { try await $0.retryRequest(id: item.id) }
      }
      await load(reset: true)
    } catch { self.error = error.userMessage }
  }
  /// At once, with Undo (D-79): sent when the Undo runs out (#206).
  private func cancel(_ item: RequestGroupItem) {
    notice = nil
    model.cancelRequestLater(item) { continuesForOthers in
      notice =
        continuesForOthers
        ? "Your request was cancelled. The shared download continues for another person." : nil
    }
  }
  private func load(reset: Bool) async {
    guard !loading else { return }
    loading = true
    error = nil
    defer { loading = false }
    let offset = reset ? 0 : (next ?? 0)
    do {
      let page = try await model.readCatalogue { try await $0.requestGroups(offset: offset) }
      if reset {
        groups = page.groups
      } else {
        for group in page.groups {
          if let index = groups.firstIndex(where: { $0.id == group.id }) {
            groups[index] = group
          } else {
            groups.append(group)
          }
        }
      }
      next = page.nextOffset
      enabled = page.downloadsEnabled
      model.serverActiveRequests = page.active
      loaded = true
    } catch is CancellationError {} catch { self.error = error.userMessage }
  }
  private func clear() async {
    guard !loading else { return }
    loading = true
    do {
      _ = try await model.performPersonal { try await $0.clearFinishedRequests() }
      model.requestedSongs.requestsChanged()
    } catch is CancellationError {
      loading = false
      return
    } catch {
      self.error = error.userMessage
      loading = false
      return
    }
    loading = false
    await load(reset: true)
  }
}

/// The playing song's favourite star: filled when it is a favourite (#100).
/// A tap changes it at once, and a library sync never greys it (#212).
struct FavouriteStar: View {
  let model: AppModel
  let track: Track
  @State private var failed = false
  private var favourite: Bool { model.favourites[track.id] ?? false }
  var body: some View {
    RoundIcon(
      label: FavouriteStates.action(favourite: favourite),
      systemImage: FavouriteStates.symbol(favourite: favourite)
    ) {
      Task { await toggle() }
    }
    .disabled(!model.favourites.canTap(track.id))
    .task(id: track.id) { await model.loadFavourite(track) }
    // Also a refusal that comes on a later send (Codex round 2).
    .onChange(of: model.favouriteRefusals) { failed = true }
    .alert("Favourite not saved", isPresented: $failed) {
      Button("OK", role: .cancel) {}
    }
  }
  private func toggle() async {
    do { try await model.tapFavourite(track) } catch is CancellationError {} catch {
      failed = true
    }
  }
}
