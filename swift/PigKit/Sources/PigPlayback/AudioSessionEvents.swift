import Foundation

#if os(iOS) || os(watchOS)
  import AVFoundation
  import Combine
#endif

/// What the system says about the shared audio session, read once for the
/// phone's player and the Watch's (#178). Each player decides what to do.
public enum AudioSessionEvent: Sendable, Equatable {
  /// `outputDisconnected`: the output went away (headphones, a Bluetooth car).
  case interruptionBegan(outputDisconnected: Bool)
  case interruptionEnded(shouldResume: Bool)
  /// `outputRemoved`: the old output became unavailable, so playback pauses.
  case routeChanged(outputRemoved: Bool)
  /// Every audio object is invalid and must be rebuilt.
  case mediaServicesReset
  /// An engine's configuration changed; `engine` says which one.
  case engineConfigurationChanged(engine: ObjectIdentifier?)
}

#if os(iOS) || os(watchOS)
  extension AudioSessionEvent {
    public static let notificationNames: [Notification.Name] = [
      AVAudioSession.interruptionNotification, AVAudioSession.routeChangeNotification,
      AVAudioSession.mediaServicesWereResetNotification, .AVAudioEngineConfigurationChange,
    ]

    public init?(_ notification: Notification) {
      let info = notification.userInfo
      switch notification.name {
      case AVAudioSession.interruptionNotification:
        if (info?[AVAudioSessionInterruptionTypeKey] as? UInt)
          == AVAudioSession.InterruptionType.began.rawValue
        {
          self = .interruptionBegan(
            outputDisconnected: (info?[AVAudioSessionInterruptionReasonKey] as? UInt)
              == AVAudioSession.InterruptionReason.routeDisconnected.rawValue)
        } else {
          self = .interruptionEnded(
            shouldResume: AVAudioSession.InterruptionOptions(
              rawValue: info?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            ).contains(.shouldResume))
        }
      case AVAudioSession.routeChangeNotification:
        self = .routeChanged(
          outputRemoved: (info?[AVAudioSessionRouteChangeReasonKey] as? UInt)
            == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue)
      case AVAudioSession.mediaServicesWereResetNotification:
        self = .mediaServicesReset
      case .AVAudioEngineConfigurationChange:
        self = .engineConfigurationChanged(
          engine: notification.object.map { ObjectIdentifier($0 as AnyObject) })
      default: return nil
      }
    }

    /// Hands each event to `handler` on the main actor while the returned
    /// subscriptions are kept.
    @MainActor public static func observe(
      _ handler: @escaping @MainActor @Sendable (AudioSessionEvent) -> Void
    ) -> [AnyCancellable] {
      notificationNames.map { name in
        NotificationCenter.default.publisher(for: name).receive(on: DispatchQueue.main).sink {
          notification in
          // Decoded here: a Notification is not Sendable, its event is.
          guard let event = AudioSessionEvent(notification) else { return }
          Task { @MainActor in handler(event) }
        }
      }
    }
  }
#endif
