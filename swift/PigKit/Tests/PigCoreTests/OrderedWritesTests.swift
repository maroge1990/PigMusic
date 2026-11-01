import PigDomain
import Testing

private actor Saved {
  var value = 0
  func set(_ new: Int) { value = new }
}

@MainActor @Test func theLatestCheckpointIsTheOneSaved() async {
  // #97: a slow older save finishing after a newer one used to win.
  let saved = Saved()
  let writes = OrderedWrites()
  writes.add {
    try? await Task.sleep(for: .milliseconds(80))
    await saved.set(1)
  }
  writes.add { await saved.set(2) }
  await writes.drain()
  try? await Task.sleep(for: .milliseconds(150))  // Long enough for any straggler.
  #expect(await saved.value == 2)
}
