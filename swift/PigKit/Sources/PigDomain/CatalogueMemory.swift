import Foundation

/// The last catalogue answer for each page key, so a revisited page draws at
/// once (#46). Bounded crudely: past `limit` answers it starts again empty.
public struct CatalogueMemory: Sendable {
  public static let limit = 400
  private var values: [String: any Sendable] = [:]
  public init() {}
  public var count: Int { values.count }
  public func peek<T: Sendable>(_ key: String, as type: T.Type = T.self) -> T? {
    values[key] as? T
  }
  public mutating func remember(_ value: any Sendable, key: String) {
    if values.count > Self.limit { values.removeAll() }
    values[key] = value
  }
  public mutating func clear() { values = [:] }
}
