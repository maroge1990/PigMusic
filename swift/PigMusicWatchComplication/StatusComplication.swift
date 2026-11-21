import PigBrand
import PigCompanion
import SwiftUI
import WidgetKit

/// PigMusic's watch-face complication (D-125, #219): "Ready to play", or how
/// many songs are still on their way, with the Watch home's icons. With one
/// on the active face, watchOS gives the Watch app background time to take
/// the songs the iPhone hands over. Tapping it opens the app.
@main struct StatusComplication: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: WatchComplicationState.widgetKind, provider: StatusProvider()) {
      entry in
      StatusView(entry: entry)
    }
    .configurationDisplayName("PigMusic")
    .description("Whether your music is ready to play on Apple Watch.")
    .supportedFamilies([
      .accessoryCircular, .accessoryCorner, .accessoryInline, .accessoryRectangular,
    ])
  }
}

struct StatusEntry: TimelineEntry {
  let date: Date
  /// What the Watch app last wrote; nil before it has run with the App Group.
  let state: WatchComplicationState?
}

/// Reads what the Watch app wrote to the App Group. The app asks WidgetKit
/// for a reload when that changes; the timeline also asks again every half
/// hour in case a reload was held back. A status that goes stale ("Can't
/// reach your iPhone") gives way to the neutral "PigMusic" when it expires.
struct StatusProvider: TimelineProvider {
  /// For the face gallery: everything there ("Ready to play").
  static let sample = WatchComplicationState(
    status: WatchLibraryStatus(songs: [], stored: [:], usedBytes: 0, limitBytes: 0),
    at: Date(timeIntervalSince1970: 0))
  static func load() -> WatchComplicationState? {
    guard
      let folder = FileManager.default.containerURL(
        forSecurityApplicationGroupIdentifier: WatchComplicationState.appGroup),
      let data = try? Data(
        contentsOf: folder.appendingPathComponent(WatchComplicationState.fileName))
    else { return nil }
    return WatchComplicationState.decode(data)
  }
  func placeholder(in context: Context) -> StatusEntry {
    StatusEntry(date: Date(), state: Self.sample)
  }
  func getSnapshot(in context: Context, completion: @escaping (StatusEntry) -> Void) {
    let now = Date()
    completion(StatusEntry(date: now, state: Self.load()?.shown(at: now) ?? Self.sample))
  }
  func getTimeline(in context: Context, completion: @escaping (Timeline<StatusEntry>) -> Void) {
    let now = Date()
    let state = Self.load()
    var entries = [StatusEntry(date: now, state: state?.shown(at: now))]
    if let expires = state?.expires, expires > now, state?.shown(at: now) != nil {
      entries.append(StatusEntry(date: expires, state: nil))
    }
    completion(Timeline(entries: entries, policy: .after(now.addingTimeInterval(30 * 60))))
  }
}

struct StatusView: View {
  @Environment(\.widgetFamily) private var family
  let entry: StatusEntry
  /// Before the app has written anything: the app's name and a note.
  private var symbol: String { entry.state?.symbol ?? "music.note" }
  private var words: String { entry.state?.words ?? "PigMusic" }
  var body: some View {
    content
      .containerBackground(for: .widget) { Color.clear }
  }
  @ViewBuilder private var content: some View {
    switch family {
    case .accessoryCircular:
      ZStack {
        AccessoryWidgetBackground()
        VStack(spacing: 0) {
          icon.font(.system(size: entry.state?.badge == nil ? 22 : 16, weight: .semibold))
          if let badge = entry.state?.badge {
            Text(badge).font(.system(size: 15, weight: .semibold)).minimumScaleFactor(0.6)
          }
        }
      }
      .accessibilityElement(children: .ignore)
      .accessibilityLabel(words)
    case .accessoryCorner:
      icon.font(.system(size: 20, weight: .semibold))
        .widgetLabel { Text(words) }
        .accessibilityLabel(words)
    case .accessoryInline:
      Label(words, systemImage: symbol)
    default:
      VStack(alignment: .leading, spacing: 2) {
        Text("PigMusic").font(.headline).widgetAccentable()
        Label(words, systemImage: symbol).font(.body)
          .lineLimit(2)
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .accessibilityElement(children: .combine)
    }
  }
  /// The status icon in the brand's accent, which tinted faces recolour.
  private var icon: some View {
    Image(systemName: symbol)
      .foregroundStyle(PigBrand.accent(.dark))
      .widgetAccentable()
  }
}
