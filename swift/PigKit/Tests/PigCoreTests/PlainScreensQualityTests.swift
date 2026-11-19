import Foundation
@testable import PigDomain
import Testing

/// Codex round 1 on batch E (D-120, #251).
struct PlainScreensQualityTests {
  private func song(_ id: String, _ seconds: Double? = 240) -> Track {
    var track = Track(id: id, title: "Song " + id, artist: "Artist")
    track.duration = seconds
    return track
  }

  /// Codex round 1 on batch E: a read that started before a save never puts
  /// the old choice back, and Account's row follows the confirmed choice.
  @Test func aStaleQualityReadNeverUndoesASave() {
    let settings = { (preferred: String?) in
      QualityChoices(
        QualitySettings(
          preferences: preferred.map { [QualityItem(scope: "system", itemId: "*", preferred: $0)] }
            ?? [],
          options: [
            QualityOption(value: "mp3_320", label: "MP3 320 kbps"),
            QualityOption(value: "aac_256", label: "AAC 256 kbps"),
          ], householdDefault: "mp3_320"))
    }
    var pick = QualityPick()
    pick.read(settings("aac_256"), token: pick.readToken())
    #expect(pick.selected == "aac_256")
    let staleRead = pick.readToken()
    let started = pick.startSave(nil)
    #expect(started)
    #expect(pick.selected == nil)
    pick.read(settings("aac_256"), token: staleRead)
    #expect(pick.selected == nil)
    pick.finishSave(succeeded: true)
    pick.read(settings("aac_256"), token: staleRead)
    #expect(pick.selected == nil)
    #expect(pick.summary == "MP3 320 kbps")
    // A refused save goes back to the confirmed choice.
    let again = pick.startSave("aac_256")
    #expect(again)
    pick.finishSave(succeeded: false)
    #expect(pick.selected == nil)
    #expect(pick.summary == "MP3 320 kbps")
    // A fresh read after the save is taken.
    pick.read(settings("aac_256"), token: pick.readToken())
    #expect(pick.selected == "aac_256")
  }
}
