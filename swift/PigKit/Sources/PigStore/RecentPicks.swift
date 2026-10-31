import PigDomain

extension LibraryStore {
  /// A recent pick as the library holds it now. A saved song carries the
  /// revision it had when picked; replaying that copy would skip a newer
  /// download on this phone (Codex, stage 1). Picks the library doesn't hold
  /// (household and catalogue music) stay as saved.
  public func current(_ pick: RecentPick) -> RecentPick {
    switch pick {
    case .song(let track):
      return ((try? self.track(id: track.id)) ?? nil).map(RecentPick.song) ?? pick
    case .album(let album):
      return ((try? self.album(id: album.id)) ?? nil).map(RecentPick.album) ?? pick
    case .artist(let artist):
      return ((try? self.artist(id: artist.id)) ?? nil).map(RecentPick.artist) ?? pick
    case .catalogueArtist:
      return pick
    }
  }
}
