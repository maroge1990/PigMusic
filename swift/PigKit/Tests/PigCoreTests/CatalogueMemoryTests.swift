import PigDomain
import Testing

struct CatalogueMemoryTests {
  @Test func keepsTheLastAnswerByKeyAndType() {
    var memory = CatalogueMemory()
    #expect(memory.peek("release:1", as: String.self) == nil)
    memory.remember("Sunrise", key: "release:1")
    memory.remember("Sunrise (Deluxe)", key: "release:1")
    #expect(memory.peek("release:1", as: String.self) == "Sunrise (Deluxe)")
    // A different type under the key is not an answer.
    #expect(memory.peek("release:1", as: Int.self) == nil)
    memory.clear()
    #expect(memory.peek("release:1", as: String.self) == nil)
  }

  @Test func startsAgainEmptyPastItsLimit() {
    var memory = CatalogueMemory()
    for index in 0...CatalogueMemory.limit { memory.remember(index, key: "page:\(index)") }
    #expect(memory.count == CatalogueMemory.limit + 1)
    memory.remember("newest", key: "page:new")
    #expect(memory.count == 1)
    #expect(memory.peek("page:new", as: String.self) == "newest")
    #expect(memory.peek("page:0", as: Int.self) == nil)
  }
}
