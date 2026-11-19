import Foundation

/// What the Watch's home and Storage & sync say about its music (D-120,
/// #252). One definition of "ready" (`WatchSongState.isReady`) feeds every
/// count, and "not ready yet" is split into its real causes.
public struct WatchSongState: Equatable, Sendable {
  public var id: String
  public var checksum: String
  public var bytes: Int64
  public var seconds: Double
  public init(id: String, checksum: String, bytes: Int64, seconds: Double) {
    self.id = id
    self.checksum = checksum
    self.bytes = bytes
    self.seconds = seconds
  }
  /// On the Watch and verified: the Watch holds this song's file under the
  /// checksum its library lists, so it plays with no iPhone or network.
  public static func isReady(id: String, checksum: String, stored: [String: String]?) -> Bool {
    stored?[id] == checksum
  }
}

/// Why a song the Watch stored was turned away, as the Watch saw it.
public enum WatchSaveProblem: Equatable, Sendable {
  /// It didn't fit under the Storage limit then; weighed again against the
  /// room under the limit now, so a larger limit or a removal clears it.
  case overLimit
  /// Apple Watch itself had no room left.
  case deviceFull
  /// Its file didn't match what the iPhone listed.
  case unreadable
}

/// What a waiting song's row says in place of "Waiting for Apple Watch".
public enum WatchSongMark: Equatable, Sendable {
  case storageFull
  case couldntSave
}

public struct WatchLibraryStatus: Equatable, Sendable {
  public enum Headline: Equatable, Sendable {
    /// Every song is on the Watch.
    case ready
    /// Songs wait that don't fit under the Storage limit.
    case storageFull
    /// Songs the Watch couldn't save (not for space).
    case couldntSave(Int)
    /// Songs wait and the iPhone is out of reach.
    case iPhoneAway
    /// Songs are still on their way from the iPhone.
    case arriving
  }
  public var total: Int
  public var ready: Int
  public var readySeconds: Double
  public var readyBytes: Int64
  /// Songs not on the Watch yet, the ones it couldn't save included.
  public var waiting: Int
  public var couldntSave: Int
  public var headline: Headline
  /// Waiting songs that are held back, and why (the rest are on their way).
  public var marks: [String: WatchSongMark]
  /// Apple Watch itself is full: a larger Storage limit won't help.
  public var deviceFull: Bool

  /// `stored`: the receipt's song id → checksum of verified files;
  /// `removing`: songs whose removal is on its way to the iPhone (left out
  /// of every count); `problems`: songs the Watch turned away this session;
  /// `iPhoneReachable`: nil when not known (no link, or not activated);
  /// `manifestApplied`: false while a newer library is announced and not here.
  public init(
    songs: [WatchSongState], stored: [String: String]?, usedBytes: Int64, limitBytes: Int64,
    removing: Set<String> = [], problems: [String: WatchSaveProblem] = [:],
    iPhoneReachable: Bool? = nil, manifestApplied: Bool = true
  ) {
    let kept = songs.filter { !removing.contains($0.id) }
    let readySongs = kept.filter {
      WatchSongState.isReady(id: $0.id, checksum: $0.checksum, stored: stored)
    }
    let waitingSongs = kept.filter {
      !WatchSongState.isReady(id: $0.id, checksum: $0.checksum, stored: stored)
    }
    total = kept.count
    ready = readySongs.count
    readySeconds = readySongs.reduce(0) { $0 + $1.seconds }
    readyBytes = readySongs.reduce(Int64(0)) { $0 + $1.bytes }
    waiting = waitingSongs.count
    let free = max(0, limitBytes - usedBytes)
    var marks: [String: WatchSongMark] = [:]
    var deviceFull = false
    for song in waitingSongs {
      switch problems[song.id] {
      case .unreadable: marks[song.id] = .couldntSave
      case .deviceFull:
        marks[song.id] = .storageFull
        deviceFull = true
      case .overLimit, nil:
        // Over the limit only while it still doesn't fit (it used to stay
        // "Storage full" after room was made).
        if song.bytes > free { marks[song.id] = .storageFull }
      }
    }
    self.marks = marks
    self.deviceFull = deviceFull
    let unreadable = marks.values.filter { $0 == .couldntSave }
    couldntSave = unreadable.count
    let full = marks.values.contains(.storageFull)
    if waitingSongs.isEmpty {
      // A new library announced and not yet here is still on its way.
      headline = manifestApplied ? .ready : (iPhoneReachable == false ? .iPhoneAway : .arriving)
    } else if full {
      headline = .storageFull
    } else if !unreadable.isEmpty {
      headline = .couldntSave(unreadable.count)
    } else if iPhoneReachable == false {
      headline = .iPhoneAway
    } else {
      headline = .arriving
    }
  }

  /// "58 of 70 songs on Apple Watch", or "70 songs" once all are there.
  public var count: String {
    waiting == 0 ? "\(total) song\(total == 1 ? "" : "s")" : "\(ready) of \(total) songs on Apple Watch"
  }

  /// The headline's words and what to do next, for the home and Storage.
  public var title: String {
    switch headline {
    case .ready: "Ready for your run"
    case .storageFull: "Storage full"
    case .couldntSave(let count): "Couldn't save \(count) song\(count == 1 ? "" : "s")"
    case .iPhoneAway: "Can't reach your iPhone"
    case .arriving: "Arriving from iPhone"
    }
  }
  public var advice: String? {
    switch headline {
    case .ready, .arriving: nil
    case .storageFull:
      deviceFull
        ? "Apple Watch has no space left. Remove songs or free up space on Apple Watch."
        : "Choose a larger Storage limit in Storage, or remove songs."
    case .couldntSave: "Refresh from iPhone in Storage to try again."
    case .iPhoneAway: "The rest arrive when your iPhone is nearby."
    }
  }
  /// The same on Storage, where the Storage limit sits below.
  public var storageAdvice: String? {
    headline == .storageFull && !deviceFull
      ? "Choose a larger Storage limit below, or remove songs." : advice
  }
}

/// The figures on Storage & sync: "1.2 GB" "of 2 GB", the gauge, and the
/// Storage limit in whole gigabytes, all counted in 1,024s so the limit
/// reads as the picker sets it.
public struct WatchStorageFigures: Equatable, Sendable {
  public static let gigabyte: Int64 = 1_073_741_824
  public static let limits = [1, 2, 4, 8]
  public var usedBytes: Int64
  public var limitBytes: Int64
  public init(usedBytes: Int64, limitBytes: Int64) {
    self.usedBytes = max(0, usedBytes)
    self.limitBytes = max(0, limitBytes)
  }
  /// Used, "1.2 GB" from 1 GB, else "350 MB".
  public var used: String { Self.size(usedBytes) }
  /// "of 2 GB".
  public var limit: String { "of " + Self.size(limitBytes) }
  /// The Storage limit picker's value, in whole gigabytes.
  public var limitGigabytes: Int { max(1, Int((limitBytes + Self.gigabyte / 2) / Self.gigabyte)) }
  /// Used share of the limit, 0 to 1.
  public var fraction: Double {
    limitBytes > 0 ? min(1, Double(usedBytes) / Double(limitBytes)) : 0
  }
  /// "60%" in the gauge.
  public var percent: String { "\(Int((fraction * 100).rounded()))%" }

  public static func size(_ bytes: Int64) -> String {
    if bytes >= gigabyte {
      let tenths = Int((Double(bytes) / Double(gigabyte) * 10).rounded())
      return tenths % 10 == 0 ? "\(tenths / 10) GB" : "\(tenths / 10).\(tenths % 10) GB"
    }
    return "\(Int((Double(bytes) / 1_048_576).rounded())) MB"
  }
}

/// The Controls page's times: "1:42" under the start and "−2:49" left.
public enum WatchClock {
  public static func elapsed(_ seconds: Double) -> String { clock(seconds) }
  public static func remaining(elapsed: Double, duration: Double) -> String {
    let left = duration.isFinite && elapsed.isFinite ? max(0, duration - max(0, elapsed)) : 0
    return "\u{2212}" + clock(left)
  }
  private static func clock(_ seconds: Double) -> String {
    guard seconds.isFinite, seconds > 0 else { return "0:00" }
    let value = Int(seconds)
    let hours = value / 3600
    let minutes = (value % 3600) / 60
    let rest = value % 60
    let twoDigits = rest < 10 ? "0\(rest)" : "\(rest)"
    if hours > 0 { return "\(hours):\(minutes < 10 ? "0" : "")\(minutes):\(twoDigits)" }
    return "\(minutes):\(twoDigits)"
  }
}
