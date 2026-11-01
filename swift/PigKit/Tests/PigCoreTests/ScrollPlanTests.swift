import PigDomain
import Testing

@Test func onlyANameWiderThanItsLineScrolls() {
  // D-65 (#108): one line each on the full player; a long name scrolls.
  #expect(!ScrollPlan(textWidth: 200, boxWidth: 300).scrolls)
  #expect(!ScrollPlan(textWidth: 300.5, boxWidth: 300).scrolls)
  #expect(!ScrollPlan(textWidth: 500, boxWidth: 0).scrolls)  // Not laid out yet.
  let long = ScrollPlan(textWidth: 420.2, boxWidth: 300)
  #expect(long.scrolls && long.distance == 121)
  #expect(abs(long.duration - 121 / ScrollPlan.speed) < 0.001)
  // A name only a little too long still moves slowly enough to read.
  #expect(ScrollPlan(textWidth: 310, boxWidth: 300).duration == 1)
}
