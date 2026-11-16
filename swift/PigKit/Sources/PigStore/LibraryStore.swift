import Foundation
import GRDB
import PigDomain

/// Each instance is permanently bound to one server/person database.
public actor LibraryStore {
  private let database: DatabaseQueue
  /// Library order ignores a leading "The", like the server's artist list.
  static let sortName = DatabaseCollation("sortname") { $0.sortingCompare($1) }
  public init(path: String) throws {
    var configuration = Configuration()
    configuration.prepareDatabase { $0.add(collation: LibraryStore.sortName) }
    let queue = try DatabaseQueue(path: path, configuration: configuration)
    var migrator = DatabaseMigrator()
    migrator.registerMigration("library-v1") { db in
      try db.execute(sql: "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
      for kind in LibraryCollection.allCases {
        try db.execute(
          sql:
            "CREATE TABLE \(kind.rawValue) (id TEXT PRIMARY KEY, name TEXT NOT NULL, payload BLOB NOT NULL)"
        )
        try db.execute(
          sql: "CREATE INDEX \(kind.rawValue)_name ON \(kind.rawValue)(name COLLATE NOCASE, id)")
      }
      try db.execute(
        sql:
          "CREATE VIRTUAL TABLE track_search USING fts5(id UNINDEXED, title, artist, album, tokenize='unicode61 remove_diacritics 2')"
      )
      try db.execute(
        sql: "CREATE INDEX tracks_album ON tracks(json_extract(CAST(payload AS TEXT),'$.album_id'))"
      )
      try db.execute(
        sql:
          "CREATE TABLE downloads (track_id TEXT PRIMARY KEY, source_revision TEXT, file_name TEXT NOT NULL, bytes INTEGER NOT NULL)"
      )
    }
    migrator.registerMigration("indexed-search-rowids-v2") { db in
      try db.execute(sql: "DELETE FROM track_search")
      try db.execute(
        sql:
          "INSERT INTO track_search(rowid,id,title,artist,album) SELECT rowid,id,json_extract(CAST(payload AS TEXT),'$.title'),json_extract(CAST(payload AS TEXT),'$.artist'),coalesce(json_extract(CAST(payload AS TEXT),'$.album'),'') FROM tracks"
      )
    }
    migrator.registerMigration("entity-search-v3") { db in
      try db.execute(
        sql:
          "CREATE VIRTUAL TABLE album_search USING fts5(id UNINDEXED, title, artist, tokenize='unicode61 remove_diacritics 2')"
      )
      try db.execute(
        sql:
          "CREATE VIRTUAL TABLE artist_search USING fts5(id UNINDEXED, name, tokenize='unicode61 remove_diacritics 2')"
      )
      try db.execute(
        sql:
          "INSERT INTO album_search(rowid,id,title,artist) SELECT rowid,id,json_extract(CAST(payload AS TEXT),'$.title'),json_extract(CAST(payload AS TEXT),'$.artist') FROM albums"
      )
      try db.execute(
        sql: "INSERT INTO artist_search(rowid,id,name) SELECT rowid,id,name FROM artists")
    }
    migrator.registerMigration("listening-outbox-v4") { db in
      try db.execute(
        sql: "CREATE TABLE listening_outbox (id TEXT PRIMARY KEY, payload BLOB NOT NULL)")
    }
    migrator.registerMigration("mutation-outbox-v5") { db in
      try db.execute(
        sql:
          "CREATE TABLE mutation_outbox (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, payload BLOB NOT NULL)"
      )
    }
    migrator.registerMigration("library-sorting-v6") { db in
      for table in ["tracks", "albums"] {
        try db.execute(
          sql:
            "CREATE INDEX \(table)_artist_title ON \(table)(json_extract(CAST(payload AS TEXT),'$.artist') COLLATE NOCASE, name COLLATE NOCASE, id)"
        )
      }
    }
    migrator.registerMigration("library-categories-v7") { db in
      // Older caches dropped artists' compilation flag: force one full resync.
      try db.execute(sql: "DELETE FROM metadata WHERE key='revision'")
    }
    migrator.registerMigration("library-sortname-v8") { db in
      // Indexes for the "The"-ignoring order (`LibraryStore.sortName`).
      for kind in LibraryCollection.allCases {
        try db.execute(
          sql: "CREATE INDEX \(kind.rawValue)_sortname ON \(kind.rawValue)(name COLLATE sortname, id)")
      }
      for table in ["tracks", "albums"] {
        try db.execute(
          sql:
            "CREATE INDEX \(table)_artist_sortname ON \(table)(json_extract(CAST(payload AS TEXT),'$.artist') COLLATE sortname, name COLLATE sortname, id)"
        )
      }
    }
    migrator.registerMigration("artist-portraits-v9") { db in
      // Artists gained `cover_sha`; a cache synced before this build (or by
      // an older app) holds artists without it, and the server will not
      // resend unchanged artists: force one full resync.
      try db.execute(sql: "DELETE FROM metadata WHERE key='revision'")
    }
    try migrator.migrate(queue)
    database = queue
  }

  public func cached<T: Decodable & Sendable>(_ key: String, as type: T.Type) throws -> T? {
    try database.read { db in
      guard
        let json = try String.fetchOne(
          db, sql: "SELECT value FROM metadata WHERE key=?", arguments: ["cache:" + key])
      else { return nil }
      return try LibraryCodec.decoder().decode(T.self, from: Data(json.utf8))
    }
  }
  public func cache<T: Encodable & Sendable>(_ value: T, key: String) throws {
    let json = String(decoding: try LibraryCodec.encoder().encode(value), as: UTF8.self)
    try database.write { db in
      try db.execute(
        sql:
          "INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        arguments: ["cache:" + key, json])
    }
  }

  public func removeCached(_ key: String) throws {
    try database.write { db in
      try db.execute(sql: "DELETE FROM metadata WHERE key=?", arguments: ["cache:" + key])
    }
  }

  /// Reads, changes and saves one cached value in a single transaction, so
  /// two changes to it can't overwrite each other (#206).
  public func updateCached<T: Codable & Sendable>(
    _ key: String, as type: T.Type, _ change: @Sendable (inout T?) -> Void
  ) throws {
    try database.write { db in
      var value = try String.fetchOne(
        db, sql: "SELECT value FROM metadata WHERE key=?", arguments: ["cache:" + key]
      ).map { try LibraryCodec.decoder().decode(T.self, from: Data($0.utf8)) }
      change(&value)
      if let value {
        let json = String(decoding: try LibraryCodec.encoder().encode(value), as: UTF8.self)
        try db.execute(
          sql:
            "INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
          arguments: ["cache:" + key, json])
      } else {
        try db.execute(sql: "DELETE FROM metadata WHERE key=?", arguments: ["cache:" + key])
      }
    }
  }

  public func revision() throws -> String? {
    try database.read {
      try String.fetchOne($0, sql: "SELECT value FROM metadata WHERE key='revision'")
    }
  }
  /// Changes only when a sync changed a cover: an album or song cover, an
  /// artist's chosen cover album, or a removed album. Nil before the first sync.
  public func artworkRevision() throws -> String? {
    try database.read {
      try String.fetchOne($0, sql: "SELECT value FROM metadata WHERE key='artwork'")
    }
  }
  public func count(_ collection: LibraryCollection) throws -> Int {
    try database.read {
      try Int.fetchOne($0, sql: "SELECT count(*) FROM \(collection.rawValue)") ?? 0
    }
  }
  public func track(id: String) throws -> Track? {
    try database.read { db in
      guard
        let data = try Data.fetchOne(
          db, sql: "SELECT payload FROM tracks WHERE id=?", arguments: [id])
      else { return nil }
      return try LibraryCodec.decoder().decode(Track.self, from: data)
    }
  }
  /// This person's own copy of `song` (#211), so a song shown from Home, a
  /// catalogue page, search or a playlist offers what the library holds:
  /// by id (or its library id), else the copy of the same recording or
  /// title that the server's matching would take (`SongIdentity.pick`).
  public func ownCopy(of song: Track) throws -> Track? {
    try database.read { db in
      func decode(_ data: Data?) throws -> Track? {
        try data.map { try LibraryCodec.decoder().decode(Track.self, from: $0) }
      }
      for id in [song.libraryTrackId, song.id].compactMap({ $0 }) {
        if let own = try decode(
          Data.fetchOne(db, sql: "SELECT payload FROM tracks WHERE id=?", arguments: [id]))
        {
          return own
        }
      }
      // Candidates: the same recording, and songs sharing every title word.
      var rows: [Data] = []
      if let recording = song.musicbrainzRecordingId, !recording.isEmpty {
        rows += try Data.fetchAll(
          db,
          sql:
            "SELECT payload FROM tracks WHERE json_extract(CAST(payload AS TEXT),'$.musicbrainz_recording_id')=? ORDER BY id LIMIT 50",
          arguments: [recording])
      }
      // Titles compare without edition qualifiers, so are looked up that way.
      let words = SongIdentity.titleKey(song.title)
      if !words.isEmpty {
        let terms = words.map { "title : \"" + $0 + "\"" }.joined(separator: " AND ")
        rows += try Data.fetchAll(
          db,
          sql:
            "SELECT t.payload FROM tracks t JOIN track_search s ON t.rowid=s.rowid WHERE track_search MATCH ? ORDER BY t.id LIMIT 200",
          arguments: [terms])
      }
      // Each with its album, for the server's order among editions.
      let tracks = try rows.compactMap { try decode($0) }
      var albums: [String: Album] = [:]
      for id in Set(tracks.compactMap(\.albumId)) {
        if let data = try Data.fetchOne(
          db, sql: "SELECT payload FROM albums WHERE id=?", arguments: [id])
        {
          albums[id] = try LibraryCodec.decoder().decode(Album.self, from: data)
        }
      }
      // Only the copy the server's matching would take; nothing it would
      // send to review (an ambiguous edition, a conflicting recording).
      return SongIdentity.pick(
        song, from: tracks.map { (track: $0, album: $0.albumId.flatMap { albums[$0] }) })
    }
  }
  /// Each known track's source revision (nil when it has none) in one query.
  /// Ids missing from the library are left out.
  public func sourceRevisions(ids: some Collection<String>) throws -> [String: String?] {
    guard !ids.isEmpty else { return [:] }
    let list = String(decoding: try JSONEncoder().encode(Array(ids)), as: UTF8.self)
    return try database.read { db in
      var result: [String: String?] = [:]
      let rows = try Row.fetchCursor(
        db,
        sql:
          "SELECT tracks.id, json_extract(CAST(tracks.payload AS TEXT),'$.source_revision') FROM tracks JOIN json_each(?) ids ON tracks.id=ids.value",
        arguments: [list])
      while let row = try rows.next() {
        result.updateValue(row[1] as String?, forKey: row[0] as String)
      }
      return result
    }
  }
  public func recordListen(_ event: ListeningEvent) throws {
    try database.write { db in
      try db.execute(
        sql: "INSERT OR IGNORE INTO listening_outbox VALUES (?,?)",
        arguments: [event.id, try LibraryCodec.encoder().encode(event)])
    }
  }
  public func pendingListens(now: Int = Int(Date().timeIntervalSince1970)) throws
    -> [ListeningEvent]
  {
    try database.read { db in
      try Row.fetchAll(db, sql: "SELECT payload FROM listening_outbox LIMIT 200").map {
        try LibraryCodec.decoder().decode(ListeningEvent.self, from: $0["payload"] as Data)
      }
    }
  }
  public func acknowledgeListens(_ ids: [String]) throws {
    try database.write { db in
      for id in ids {
        try db.execute(sql: "DELETE FROM listening_outbox WHERE id=?", arguments: [id])
      }
    }
  }
  public func tracks(
    query: String = "", albumId: String? = nil, offset: Int = 0, limit: Int = 100,
    sort: LibrarySort = .title
  )
    throws -> [Track]
  {
    guard offset >= 0, (1...200).contains(limit) else { throw StoreSearchFailure.invalidPage }
    return try database.read { db in
      let rows: [Row]
      if !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        let terms = query.split(whereSeparator: { $0.isWhitespace }).map {
          "\"" + $0.replacingOccurrences(of: "\"", with: "\"\"") + "\"*"
        }.joined(separator: " AND ")
        rows = try Row.fetchAll(
          db,
          sql:
            "SELECT t.payload FROM tracks t JOIN track_search s ON t.id=s.id WHERE track_search MATCH ? ORDER BY rank LIMIT ? OFFSET ?",
          arguments: [terms, limit, offset])
      } else if let albumId {
        rows = try Row.fetchAll(
          db,
          sql:
            "SELECT payload FROM tracks WHERE json_extract(CAST(payload AS TEXT),'$.album_id')=? ORDER BY CAST(json_extract(CAST(payload AS TEXT),'$.disc_number') AS INTEGER), CAST(json_extract(CAST(payload AS TEXT),'$.track_number') AS INTEGER), id",
          arguments: [albumId])
      } else {
        rows = try Row.fetchAll(
          db, sql: "SELECT payload FROM tracks ORDER BY \(Self.order(sort)) LIMIT ? OFFSET ?",
          arguments: [limit, offset])
      }
      return try rows.map {
        try LibraryCodec.decoder().decode(Track.self, from: $0["payload"] as Data)
      }
    }
  }
  public func albums(artistId: String, offset: Int = 0, limit: Int = 100) throws -> [Album] {
    guard offset >= 0, (1...200).contains(limit) else { throw StoreSearchFailure.invalidPage }
    return try database.read { db in
      let rows = try Row.fetchAll(
        db,
        sql:
          "SELECT payload FROM albums WHERE id IN (SELECT value FROM artists, json_each(CAST(artists.payload AS TEXT), '$.album_ids') WHERE artists.id=?) ORDER BY name COLLATE sortname,id LIMIT ? OFFSET ?",
        arguments: [artistId, limit, offset])
      return try rows.map {
        try LibraryCodec.decoder().decode(Album.self, from: $0["payload"] as Data)
      }
    }
  }

  public func searchPage(query: String, offset: Int = 0, limit: Int = 100) throws
    -> LibrarySearchPage
  {
    guard offset >= 0, offset <= 1_000_000, (1...200).contains(limit) else {
      throw StoreSearchFailure.invalidPage
    }
    let query = SearchText.clean(query)
    guard !query.isEmpty else { return LibrarySearchPage() }
    let terms = query.split(whereSeparator: { $0.isWhitespace }).map {
      "\"" + $0.replacingOccurrences(of: "\"", with: "\"\"") + "\"*"
    }.joined(separator: " AND ")
    return try database.read { db in
      func page<T: Decodable>(_ table: String, _ index: String, _ type: T.Type) throws -> ([T], Int)
      {
        let rows = try Row.fetchAll(
          db,
          sql:
            "SELECT t.payload FROM \(table) t JOIN \(index) s ON t.rowid=s.rowid WHERE \(index) MATCH ? ORDER BY rank, t.id LIMIT ? OFFSET ?",
          arguments: [terms, limit, offset])
        let count =
          try Int.fetchOne(
            db, sql: "SELECT count(*) FROM \(index) WHERE \(index) MATCH ?", arguments: [terms])
          ?? 0
        return (
          try rows.map { try LibraryCodec.decoder().decode(T.self, from: $0["payload"] as Data) },
          count
        )
      }
      let (tracks, trackCount) = try page("tracks", "track_search", Track.self)
      let (albums, albumCount) = try page("albums", "album_search", Album.self)
      let (artists, artistCount) = try page("artists", "artist_search", Artist.self)
      let end = offset + limit
      let revision =
        try String.fetchOne(db, sql: "SELECT value FROM metadata WHERE key='revision'") ?? ""
      return LibrarySearchPage(
        tracks: tracks, albums: albums, artists: artists, offset: offset,
        nextOffset: end < max(trackCount, albumCount, artistCount) ? end : nil, revision: revision)
    }
  }

  /// Albums of one category ("album", "musical", "soundtrack" or "compilation").
  public func albums(
    category: String = "album", offset: Int = 0, limit: Int = 100, sort: LibrarySort = .title
  ) throws -> [Album] {
    try read(
      .albums, where: Self.albumCategory, arguments: [category], offset: offset, limit: limit,
      sort: sort)
  }
  /// Artists with music of their own; compilation-only credits stay hidden, as on the web.
  public func artists(offset: Int = 0, limit: Int = 100, sort: LibrarySort = .title) throws
    -> [Artist]
  {
    try read(.artists, where: Self.ownArtist, offset: offset, limit: limit, sort: sort)
  }
  /// One saved artist by ID.
  public func album(id: String) throws -> Album? {
    let found: [Album] = try read(.albums, where: "id=?", arguments: [id], offset: 0, limit: 1)
    return found.first
  }
  public func artist(id: String) throws -> Artist? {
    let found: [Artist] = try read(.artists, where: "id=?", arguments: [id], offset: 0, limit: 1)
    return found.first
  }
  /// The saved artist with this exact name, ignoring case; nil when none or several match.
  public func artist(named name: String) throws -> Artist? {
    let found: [Artist] = try read(
      .artists, where: "name=? COLLATE NOCASE", arguments: [name], offset: 0, limit: 2)
    return found.count == 1 ? found[0] : nil
  }
  public func albumCategoryCounts() throws -> [String: Int] {
    try database.read { db in
      let rows = try Row.fetchAll(
        db, sql: "SELECT \(Self.categoryExpression) AS category, count(*) AS n FROM albums GROUP BY 1")
      return Dictionary(uniqueKeysWithValues: rows.map { ($0["category"] as String, $0["n"] as Int) })
    }
  }
  public func ownArtistCount() throws -> Int {
    try database.read {
      try Int.fetchOne($0, sql: "SELECT count(*) FROM artists WHERE \(Self.ownArtist)") ?? 0
    }
  }
  private static let categoryExpression =
    "coalesce(json_extract(CAST(payload AS TEXT),'$.category'),'album')"
  private static let albumCategory = categoryExpression + "=?"
  private static let ownArtist =
    "NOT coalesce(json_extract(CAST(payload AS TEXT),'$.compilation_only'),0)"
  public func playlists() throws -> [Playlist] { try read(.playlists, offset: 0, limit: 10000) }

  private func read<T: Decodable>(
    _ kind: LibraryCollection, where filter: String = "1", arguments: [String] = [],
    offset: Int, limit: Int, sort: LibrarySort = .title
  ) throws -> [T] {
    try database.read { db in
      try Row.fetchAll(
        db,
        sql:
          "SELECT payload FROM \(kind.rawValue) WHERE \(filter) ORDER BY \(Self.order(sort)) LIMIT ? OFFSET ?",
        arguments: StatementArguments(arguments) + [limit, offset]
      )
      .map { try LibraryCodec.decoder().decode(T.self, from: $0["payload"] as Data) }
    }
  }

  private static func order(_ sort: LibrarySort) -> String {
    switch sort {
    case .title: "name COLLATE sortname, id"
    case .titleDescending: "name COLLATE sortname DESC, id"
    case .artist:
      "json_extract(CAST(payload AS TEXT),'$.artist') COLLATE sortname, name COLLATE sortname, id"
    }
  }

  /// Transactional replacement preserves the old valid cache if any row fails.
  public func replace(_ snapshot: LibrarySnapshot) throws {
    try database.write { db in
      let before = try Self.coverDigest(db)
      for kind in LibraryCollection.allCases { try db.execute(sql: "DELETE FROM \(kind.rawValue)") }
      for index in ["track_search", "album_search", "artist_search"] {
        try db.execute(sql: "DELETE FROM \(index)")
      }
      try Self.upsert(snapshot, db: db)
      if try Self.coverDigest(db) != before { try Self.touchArtwork(db) }
    }
  }
  public func apply(_ delta: LibraryDelta) throws {
    try database.write { db in
      let artworkChanged = try Self.coversChange(delta, db: db)
      for (kind, ids) in [
        (LibraryCollection.tracks, delta.deletes.tracks), (.albums, delta.deletes.albums),
        (.artists, delta.deletes.artists), (.playlists, delta.deletes.playlists),
      ] {
        for id in ids {
          let index: String? =
            switch kind {
            case .tracks: "track_search"
            case .albums: "album_search"
            case .artists: "artist_search"
            case .playlists: nil
            }
          if let index {
            try db.execute(
              sql:
                "DELETE FROM \(index) WHERE rowid=(SELECT rowid FROM \(kind.rawValue) WHERE id=?)",
              arguments: [id])
          }
          try db.execute(sql: "DELETE FROM \(kind.rawValue) WHERE id=?", arguments: [id])
        }
      }
      try Self.upsert(
        .init(
          revision: delta.revision, tracks: delta.upserts.tracks, albums: delta.upserts.albums,
          artists: delta.upserts.artists, playlists: delta.upserts.playlists), db: db)
      if artworkChanged { try Self.touchArtwork(db) }
    }
  }
  /// Whether applying `delta` would change any cover the library draws. One
  /// query per kind reads every stored cover reference the delta touches.
  private static func coversChange(_ delta: LibraryDelta, db: Database) throws -> Bool {
    if !delta.deletes.albums.isEmpty { return true }
    func differs(_ table: String, _ key: String, _ values: [(id: String, value: String?)])
      throws -> Bool
    {
      guard !values.isEmpty else { return false }
      let list = String(decoding: try JSONEncoder().encode(values.map(\.id)), as: UTF8.self)
      var stored: [String: String?] = [:]
      let rows = try Row.fetchCursor(
        db,
        sql:
          "SELECT \(table).id, json_extract(CAST(\(table).payload AS TEXT),'$.\(key)') FROM \(table) JOIN json_each(?) ids ON \(table).id=ids.value",
        arguments: [list])
      while let row = try rows.next() {
        stored.updateValue(row[1] as String?, forKey: row[0] as String)
      }
      return values.contains { id, value in
        guard let current = stored[id] else { return value != nil }
        return current != value
      }
    }
    return try differs("albums", "cover_sha", delta.upserts.albums.map { ($0.id, $0.coverSha) })
      || differs("tracks", "cover_sha", delta.upserts.tracks.map { ($0.id, $0.coverSha) })
      || differs(
        "artists", "cover_album_id", delta.upserts.artists.map { ($0.id, $0.coverAlbumId) })
      || differs("artists", "cover_sha", delta.upserts.artists.map { ($0.id, $0.coverSha) })
  }
  /// Every cover reference in the library, for comparing a full replacement.
  private static func coverDigest(_ db: Database) throws -> [String] {
    try [
      ("albums", "cover_sha"), ("tracks", "cover_sha"), ("artists", "cover_album_id"),
      ("artists", "cover_sha"),
    ].map {
      table, key in
      try String.fetchOne(
        db,
        sql:
          "SELECT coalesce(group_concat(v, ','), '') FROM (SELECT id || '=' || coalesce(json_extract(CAST(payload AS TEXT),'$.\(key)'),'') AS v FROM \(table) ORDER BY id)"
      ) ?? ""
    }
  }
  private static func touchArtwork(_ db: Database) throws {
    try db.execute(
      sql:
        "INSERT INTO metadata(key,value) VALUES ('artwork',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      arguments: [UUID().uuidString])
  }
  private static func upsert(_ snapshot: LibrarySnapshot, db: Database) throws {
    for track in snapshot.tracks {
      try save(track, kind: .tracks, name: track.title, db: db)
      try db.execute(
        sql: "DELETE FROM track_search WHERE rowid=(SELECT rowid FROM tracks WHERE id=?)",
        arguments: [track.id])
      try db.execute(
        sql:
          "INSERT INTO track_search(rowid,id,title,artist,album) VALUES ((SELECT rowid FROM tracks WHERE id=?),?,?,?,?)",
        arguments: [track.id, track.id, track.title, track.artist, track.album ?? ""])
    }
    for album in snapshot.albums {
      try save(album, kind: .albums, name: album.title, db: db)
      try db.execute(
        sql: "DELETE FROM album_search WHERE rowid=(SELECT rowid FROM albums WHERE id=?)",
        arguments: [album.id])
      try db.execute(
        sql:
          "INSERT INTO album_search(rowid,id,title,artist) VALUES ((SELECT rowid FROM albums WHERE id=?),?,?,?)",
        arguments: [album.id, album.id, album.title, album.artist])
    }
    for artist in snapshot.artists {
      try save(artist, kind: .artists, name: artist.name, db: db)
      try db.execute(
        sql: "DELETE FROM artist_search WHERE rowid=(SELECT rowid FROM artists WHERE id=?)",
        arguments: [artist.id])
      try db.execute(
        sql:
          "INSERT INTO artist_search(rowid,id,name) VALUES ((SELECT rowid FROM artists WHERE id=?),?,?)",
        arguments: [artist.id, artist.id, artist.name])
    }
    for playlist in snapshot.playlists {
      try save(playlist, kind: .playlists, name: playlist.name, db: db)
    }
    try db.execute(
      sql:
        "INSERT INTO metadata(key,value) VALUES ('revision',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      arguments: [snapshot.revision])
  }
  private static func save<T: Encodable & Identifiable>(
    _ value: T, kind: LibraryCollection, name: String, db: Database
  ) throws where T.ID == String {
    let payload = try LibraryCodec.encoder().encode(value)
    try db.execute(
      sql:
        "INSERT INTO \(kind.rawValue)(id,name,payload) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,payload=excluded.payload",
      arguments: [value.id, name, payload])
  }
}

public enum StoreSearchFailure: Error, Sendable { case invalidPage }

extension LibraryStore {
  public func enqueueMutation(_ mutation: OfflineMutation) throws {
    let payload = try LibraryCodec.encoder().encode(PendingMutation(mutation: mutation))
    try database.write { db in
      guard try Int.fetchOne(db, sql: "SELECT count(*) FROM mutation_outbox") ?? 0 < 1000 else {
        throw MutationRejection.rejected
      }
      try db.execute(
        sql: "INSERT OR IGNORE INTO mutation_outbox(id,payload) VALUES (?,?)",
        arguments: [mutation.id, payload])
    }
  }
  public func pendingMutations() throws -> [PendingMutation] {
    try database.read { db in
      try Data.fetchAll(db, sql: "SELECT payload FROM mutation_outbox ORDER BY sequence").map {
        try LibraryCodec.decoder().decode(PendingMutation.self, from: $0)
      }
    }
  }
  public func acknowledgeMutation(_ id: String) throws {
    try database.write {
      try $0.execute(sql: "DELETE FROM mutation_outbox WHERE id=?", arguments: [id])
    }
  }
  public func updateMutation(_ pending: PendingMutation) throws {
    let payload = try LibraryCodec.encoder().encode(pending)
    try database.write {
      try $0.execute(
        sql: "UPDATE mutation_outbox SET payload=? WHERE id=?", arguments: [payload, pending.id])
    }
  }
}
