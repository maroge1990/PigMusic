import PigDomain
import Testing

/// Codex round 1 on batch D: the outbox's one notice keeps the paused
/// sentence beside a refusal or an exclusion.
struct RequestNoticeTests {
  @Test func pausedBesideAnExclusion() {
    #expect(
      RequestNotice.text(refused: 0, reason: nil, excluded: "First", paused: true)
        == "Couldn't add First: it's excluded from this server's downloads. Paused. Resume it in Downloads."
    )
  }

  @Test func pausedBesideARefusal() {
    #expect(
      RequestNotice.text(refused: 1, reason: "No.", excluded: nil, paused: true)
        == "No. Paused. Resume it in Downloads.")
    #expect(
      RequestNotice.text(refused: 2, reason: "No.", excluded: nil, paused: true)
        == "Couldn't add 2 songs: No. Paused. Resume it in Downloads.")
  }

  @Test func eachAlone() {
    #expect(RequestNotice.text(refused: 0, reason: nil, excluded: nil, paused: false) == nil)
    #expect(
      RequestNotice.text(refused: 0, reason: nil, excluded: nil, paused: true)
        == "Paused. Resume it in Downloads.")
    #expect(RequestNotice.text(refused: 1, reason: "No.", excluded: "First", paused: false) == "No.")
  }
}
