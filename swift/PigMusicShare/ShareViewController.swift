import Observation
import PigDomain
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Share → PigMusic (D-70, option C): a song shared from the Shazam app or
/// Apple Music joins PigMusic's "Shazamed" list. The extension only notes the
/// link in the App Group; it never talks to the server, and the app looks the
/// song up the next time it opens. Nothing is downloaded.
final class ShareViewController: UIViewController {
  private let status = ShareStatus()

  override func viewDidLoad() {
    super.viewDidLoad()
    let card = ShareCard(status: status) { [weak self] in self?.close() }
    let host = UIHostingController(rootView: card)
    addChild(host)
    host.view.frame = view.bounds
    host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
    host.view.backgroundColor = .clear
    view.addSubview(host.view)
    host.didMove(toParent: self)
    let items = extensionContext?.inputItems.compactMap { $0 as? NSExtensionItem } ?? []
    Task { await receive(items) }
  }

  private func receive(_ items: [NSExtensionItem]) async {
    var urls: [URL] = []
    var texts: [String] = items.compactMap { $0.attributedContentText?.string }
    for provider in items.flatMap({ $0.attachments ?? [] }) {
      if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier),
        let url = await Self.load(URL.self, from: provider)
      {
        urls.append(url)
      } else if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier),
        let text = await Self.load(String.self, from: provider)
      {
        texts.append(text)
      }
    }
    guard let share = ShazamShare.parse(urls: urls, texts: texts, at: Date()) else {
      status.state = .notASong
      return
    }
    guard
      let container = FileManager.default.containerURL(
        forSecurityApplicationGroupIdentifier: ShazamShareFolder.appGroup)
    else {
      status.state = .failed
      return
    }
    do {
      try ShazamShareQueue(directory: ShazamShareFolder.queue(in: container)).append(share)
      status.state = .added
      try? await Task.sleep(for: .seconds(1.2))
      close()
    } catch {
      status.state = .failed
    }
  }

  private static func load<T: _ObjectiveCBridgeable & Sendable>(
    _ type: T.Type, from provider: NSItemProvider
  ) async -> T? where T._ObjectiveCType: NSItemProviderReading {
    await withCheckedContinuation { continuation in
      _ = provider.loadObject(ofClass: type) { value, _ in continuation.resume(returning: value) }
    }
  }

  private func close() {
    extensionContext?.completeRequest(returningItems: nil)
  }
}

@MainActor @Observable final class ShareStatus {
  enum State { case working, added, notASong, failed }
  var state = State.working
}

private struct ShareCard: View {
  let status: ShareStatus
  let done: () -> Void
  var body: some View {
    VStack(spacing: 14) {
      switch status.state {
      case .working:
        ProgressView().controlSize(.large)
      case .added:
        Image(systemName: "checkmark.circle.fill").font(.system(size: 44))
          .foregroundStyle(.tint).accessibilityHidden(true)
        Text("Added to PigMusic's Shazamed list").font(.headline)
          .multilineTextAlignment(.center)
        Text("Open PigMusic to see whether your server has it. Nothing downloads until you ask.")
          .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
      case .notASong:
        message(
          "This isn't a song link",
          "PigMusic can add songs shared from the Shazam app or Apple Music.")
      case .failed:
        message(
          "PigMusic couldn't add this song",
          "Try sharing it again in a moment.")
      }
    }
    .padding(24).frame(maxWidth: 360)
    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 22))
    .frame(maxWidth: .infinity, maxHeight: .infinity)
  }
  @ViewBuilder private func message(_ title: String, _ detail: String) -> some View {
    Image(systemName: "exclamationmark.circle").font(.system(size: 44))
      .foregroundStyle(.secondary).accessibilityHidden(true)
    Text(title).font(.headline).multilineTextAlignment(.center)
    Text(detail).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
    Button("Done", action: done).buttonStyle(.borderedProminent)
  }
}
