import Foundation
import PigDomain

/// A background refresh for the Watch copies the server was still preparing
/// (#219). iOS may launch the app for it with no scene (#223): nothing has
/// restored the signed-in account then, so the refresh restores it first,
/// from what the phone saved and without the network, before handing the
/// queued copies on. A restoration that cannot finish now (a keychain still
/// locked before the first unlock) asks for another refresh; it never signs
/// the account out.
@MainActor public enum BackgroundRefresh {
  public enum Restoration: Equatable, Sendable {
    /// The saved account is attached, under this account generation.
    case attached(generation: Int)
    /// No account is saved on this phone: nothing waits.
    case signedOut
    /// The saved account could not be restored now. `generation` is the
    /// account generation the restoration itself left; another account or a
    /// sign-out since moves it on, and then nothing is asked for.
    case unavailable(generation: Int)
  }
  public enum Result: Equatable, Sendable {
    /// The account is there: the Watch copies were handed on (the hand-off
    /// asks for another refresh while copies still wait).
    case handedOff
    /// The account could not be restored now: another refresh was asked for.
    case rescheduled
    /// Nothing to do: signed out, another account, or back in the foreground,
    /// whose own work takes over.
    case stopped
    /// The Watch has received nothing for too long (D-113): nothing is
    /// restored, handed on or asked for until the app is opened again.
    case limitReached
  }

  /// Runs one refresh. `attached` is the generation of the account this
  /// launch already holds, nil when it has none yet. Every step after an
  /// await checks the account is still the same one and the app still away.
  /// `mayStart` is the Watch's background limit (`WatchBackgroundWork`),
  /// asked before anything starts and again before each later step.
  public static func run(
    attached: Int?, fence: AccountFence, inForeground: () -> Bool,
    mayStart: () -> Bool = { true },
    restore: () async -> Restoration, handOff: () async -> Void, reschedule: () -> Void
  ) async -> Result {
    guard !inForeground() else { return .stopped }
    guard mayStart() else { return .limitReached }
    let generation: Int
    if let attached {
      generation = attached
    } else {
      switch await restore() {
      case .attached(let restored):
        generation = restored
      case .signedOut:
        return .stopped
      case .unavailable(let left):
        guard fence.isCurrent(left), !inForeground() else { return .stopped }
        guard mayStart() else { return .limitReached }
        reschedule()
        return .rescheduled
      }
    }
    guard fence.isCurrent(generation), !inForeground() else { return .stopped }
    guard mayStart() else { return .limitReached }
    await handOff()
    return .handedOff
  }
}
