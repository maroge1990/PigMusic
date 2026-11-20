import PigBrand
import PigDomain
import SwiftUI

/// Changes made offline that wait to reach the server, each in plain words
/// with its cover and state: Waiting to send, or Couldn't send. Try again.
/// (D-120). Nothing explains itself in a paragraph (D-101).
struct PendingEditsView: View {
  let model: AppModel
  @State private var loading = false
  /// The change a Discard asks about.
  @State private var discarding: String?
  /// The playlist a change's Go to playlist opens.
  @State private var reviewing: Playlist?
  /// What the last Discard did: "Discarded", "Already sent" or "Couldn't
  /// discard. Try again."
  @State private var result: String?
  var body: some View {
    List {
      if let result {
        StatusText(
          result,
          kind: [.failed, .unknown].map(PendingChangeWords.discarded).contains(result)
            ? .error : .info)
      }
      if model.pendingEdits.isEmpty {
        PigQuietState("All changes sent").listRowBackground(Color.clear)
      } else {
        // Withdrawn changes are never sent again: not counted.
        let sendable = model.pendingEdits.count { !$0.withdrawn }
        if sendable > 0 {
          Section {
            Button(
              PendingChangeWords.sendLabel(count: sendable), systemImage: "arrow.up.circle"
            ) {
              Task {
                loading = true
                _ = await model.flushEdits()
                await model.refresh()
                loading = false
              }
            }.disabled(loading)
          }
        }
        Section {
          ForEach(model.pendingEdits) { pending in
            PendingChangeRow(
              model: model, pending: pending, busy: loading,
              retry: { Task { await model.retryEdit(pending.id) } },
              discard: { discarding = pending.id }, open: { reviewing = $0 },
              forget: { Task { await model.forgetEdit(pending.id) } })
          }
        }
      }
    }.pigCanvas().navigationTitle("Changes not sent yet").navigationBarTitleDisplayMode(.inline)
      .navigationDestination(item: $reviewing) { PlaylistView(model: model, playlist: $0) }
      .refreshable {
        _ = await model.flushEdits()
        await model.refresh()
      }
      .confirmationDialog(
        "Discard this change?",
        isPresented: Binding(get: { discarding != nil }, set: { if !$0 { discarding = nil } }),
        titleVisibility: .visible
      ) {
        if let id = discarding {
          Button("Discard this change", role: .destructive) {
            Task { result = await model.discardEdit(id) }
            discarding = nil
          }
        }
      }
  }
}

/// One change: what it does, to which song or playlist, and its state. A
/// change that couldn't be sent offers Try again; ⋯ holds the rest.
private struct PendingChangeRow: View {
  let model: AppModel
  let pending: PendingMutation
  let busy: Bool
  let retry: () -> Void
  let discard: () -> Void
  let open: (Playlist) -> Void
  /// Remove from this list: a withdrawn change, forgotten on this iPhone.
  let forget: () -> Void
  @Environment(\.colorScheme) private var scheme
  // The ⋯ grows with Dynamic Type (#162).
  @ScaledMetric(relativeTo: .body) private var moreWidth: CGFloat = 32
  private var playlist: Playlist? {
    pending.mutation.playlistId.flatMap { id in model.playlists.first { $0.id == id } }
  }
  var body: some View {
    let words = PendingChangeWords(pending, playlistName: playlist?.name)
    HStack(spacing: 12) {
      thumb(words)
      VStack(alignment: .leading, spacing: 2) {
        Text(words.title).lineLimit(2)
        if let detail = words.detail {
          Text(detail).font(PigType.footnote).foregroundStyle(.secondary).lineLimit(1)
        }
        Label(words.status, systemImage: words.failed ? "exclamationmark.circle" : "clock")
          .font(PigType.footnote)
          .foregroundStyle(words.failed ? PigBrand.danger(scheme) : PigBrand.secondary(scheme))
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .accessibilityElement(children: .combine)
      if words.retryable {
        Button(action: retry) {
          Image(systemName: "arrow.clockwise").frame(width: 44, height: 44)
        }
        .buttonStyle(.borderless).disabled(busy).accessibilityLabel("Try again")
      }
      Menu {
        if let playlist {
          Button("Go to playlist", systemImage: "music.note.list") { open(playlist) }
        }
        if words.withdrawn {
          // Never sent again; this only forgets it here.
          Button("Remove from this list", systemImage: "xmark", action: forget).disabled(busy)
        } else {
          // Asks first: a discarded change can't be brought back (D-79, #183).
          Button("Discard this change", systemImage: "xmark", role: .destructive, action: discard)
            .disabled(busy)
        }
      } label: {
        Image(systemName: "ellipsis").frame(minWidth: moreWidth, minHeight: 44)
          .foregroundStyle(.secondary)
      }
      .buttonStyle(.borderless).accessibilityLabel("More actions for this change")
    }
  }
  @ViewBuilder private func thumb(_ words: PendingChangeWords) -> some View {
    if let track = pending.mutation.track {
      ArtworkThumb(model: model, source: track.artworkSource, title: track.title)
    } else {
      SymbolThumb(systemImage: words.symbol)
    }
  }
}
