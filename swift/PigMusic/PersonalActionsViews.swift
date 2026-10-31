import PigAPI
import PigBrand
import PigDomain
import SwiftUI

struct SongActionsView: View {
  let model: AppModel
  let track: Track
  @Environment(\.dismiss) private var dismiss
  @State private var addedToLibrary = false
  private var favourite: Bool? { model.favourites[track.id] }
  @State private var loading = false
  @State private var message: String?
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
              favourite ? "Remove from favourites" : "Add to favourites",
              systemImage: favourite ? "star.slash" : "star"
            ) {
              Task { await changeFavourite(!favourite) }
            }.disabled(loading || !model.canMutate)
          } else {
            Button("Load favourite status") { Task { await loadFavourite() } }.disabled(loading)
          }
          if addedToLibrary {
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
          } else if track.serverState == "not_in_library" {
            Button(
              track.id.hasPrefix("mix:") ? "Add to my library" : "Request song",
              systemImage: "arrow.down.circle"
            ) {
              Task { await request() }
            }.disabled(loading || !model.canMutate)
          }
        }
        Section {
          if model.player.queue.current != nil {
            Button("Play next") { model.player.enqueue([track], next: true) }
            Button("Add to queue") { model.player.enqueue([track]) }
          }
          NavigationLink("Add to playlist") { AddToPlaylistView(model: model, track: track) }
          if track.serverState == "in_library" || track.serverState == nil {
            Button("Download original", systemImage: "arrow.down.circle") {
              Task { await model.download([track]) }
            }
            Button("Send to Apple Watch", systemImage: "applewatch") {
              Task { message = await model.watch?.enqueue([track]) }
            }
            Button("Download smaller AAC copy") {
              Task { await model.download([track], quality: "aac") }
            }
            NavigationLink("Remove music") { RemovalView(model: model, tracks: [track]) }
          }
        } footer: {
          Text(
            "Favourites do not download music. Requests are sent to your server and are separate from downloads to this device."
          )
        }
        if loading { ProgressView() }
        if let message { Section { Text(message).font(PigType.footnote) } }
      }.pigCanvas().navigationTitle("Song actions").navigationBarTitleDisplayMode(.inline)
        .toolbar { Button("Done") { dismiss() }.disabled(loading) }
        .task { await loadFavourite() }
    }.interactiveDismissDisabled(model.mutationActive)
  }
  private func loadFavourite() async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    await model.loadFavourite(track)
  }
  private func changeFavourite(_ value: Bool) async {
    guard !loading else { return }
    loading = true
    message = nil
    defer { loading = false }
    do {
      let confirmed = try await model.setFavourite(track, value)
      message =
        confirmed
        ? (value ? "Added to favourites." : "Removed from favourites.")
        : "Favourite edit saved on this phone; waiting to sync."
    } catch is CancellationError {} catch { message = error.userMessage }
  }
  private func add() async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    do {
      let count = try await model.performPersonal {
        try await $0.addMembership(trackIds: [track.id])
      }
      message = count > 0 ? "Added to your library." : "Already in your library."
      addedToLibrary = true
    } catch is CancellationError {} catch { message = error.userMessage }
  }
  private func request() async {
    guard !loading else { return }
    loading = true
    defer { loading = false }
    do {
      let result = try await model.performPersonal { try await $0.requestMusic([track]) }
      message =
        "Server confirmed: \(result.created) new, \(result.existing) existing, \(result.skipped) skipped."
      if !result.downloadsEnabled {
        message! += " Server downloads are disabled; requests will wait."
      }
    } catch is CancellationError {} catch { message = error.userMessage }
  }
}

/// Server requests, one per album request (a lone song alone), unfinished
/// first. Opening a request shows its songs with their source and quality.
struct RequestsView: View {
  let model: AppModel
  @State private var groups: [RequestGroup] = []
  @State private var next: Int?
  @State private var loading = false
  @State private var loaded = false
  @State private var enabled = true
  @State private var error: String?
  var body: some View {
    List {
      if !enabled {
        Text("Server downloads are disabled. Requests can wait until downloads are enabled.").font(
          PigType.footnote)
      }
      if loading && !loaded { ProgressView("Loading requests…") }
      if let error { Text(error).font(PigType.footnote) }
      if loaded && groups.isEmpty { Text("No music requests").foregroundStyle(.secondary) }
      ForEach(groups) { group in
        DisclosureGroup {
          ForEach(group.items) { item in songRow(item) }
          let failed = group.items.filter { ["failed", "cancelled"].contains($0.status) }
          if failed.count > 1 {
            Button("Retry \(failed.count) songs") { Task { await retry(failed) } }
              .disabled(loading || !model.canMutate)
          }
        } label: {
          HStack(spacing: 12) {
            ArtworkThumb(
              model: model, source: group.releaseId.map { .release($0) }, title: group.title,
              size: 48)
            VStack(alignment: .leading, spacing: 2) {
              Text(group.title).lineLimit(1)
              Text(group.artist).font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
              Text(group.summary).font(PigType.caption).foregroundStyle(.secondary)
              if group.state != "done" {
                ProgressView(value: min(1, max(0, group.progress)))
              }
            }
          }
          .accessibilityElement(children: .combine)
        }
      }
      if next != nil {
        Button("Load more requests") { Task { await load(reset: false) } }.disabled(loading)
      }
    }.pigCanvas().navigationTitle("Music requests")
      .toolbar {
        Button("Refresh", systemImage: "arrow.clockwise") { Task { await load(reset: true) } }
          .disabled(loading)
        Button("Clear finished") { Task { await clear() } }.disabled(loading || !model.canMutate)
      }
      .task { await load(reset: true) }
      // Live: the server's request queue changed.
      .onChange(of: model.requestsRevision) { Task { await load(reset: true) } }
  }
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
        Text(error).font(PigType.caption).foregroundStyle(.secondary)
      }
      if ["failed", "cancelled"].contains(item.status) {
        Button("Retry request") { Task { await retry([item]) } }
          .disabled(loading || !model.canMutate)
      }
      if ["waiting_for_sources", "searching", "downloading", "validating"].contains(item.status) {
        Button("Cancel my request", role: .destructive) { Task { await cancel(item) } }
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
  private func cancel(_ item: RequestGroupItem) async {
    do {
      let result = try await model.performPersonal { try await $0.cancelRequest(id: item.id) }
      await load(reset: true)
      if result.continuesForOthers {
        self.error =
          "Your request was cancelled. The shared download continues for another person."
      }
    } catch { self.error = error.userMessage }
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
struct FavouriteStar: View {
  let model: AppModel
  let track: Track
  @State private var saving = false
  @State private var failed = false
  private var favourite: Bool { model.favourites[track.id] ?? false }
  var body: some View {
    RoundIcon(
      label: favourite ? "Remove from favourites" : "Add to favourites",
      systemImage: favourite ? "star.fill" : "star"
    ) {
      Task { await toggle() }
    }
    .disabled(saving || !model.canMutate || model.favourites[track.id] == nil)
    .task(id: track.id) { await model.loadFavourite(track) }
    .alert("Favourite not saved", isPresented: $failed) {
      Button("OK", role: .cancel) {}
    }
  }
  private func toggle() async {
    guard !saving, let current = model.favourites[track.id] else { return }
    saving = true
    defer { saving = false }
    do { _ = try await model.setFavourite(track, !current) } catch is CancellationError {} catch {
      failed = true
    }
  }
}
