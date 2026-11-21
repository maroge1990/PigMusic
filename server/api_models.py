"""Stable, path-free response shapes shared by web and native clients."""

from typing import Literal
from pydantic import BaseModel, Field

ServerState = Literal[
    "not_in_library", "household_available", "requested", "in_library", "failed"
]


class TrackDTO(BaseModel):
    id: str = ""
    title: str
    artist: str
    album: str = ""
    album_artist: str = ""
    year: str | int = ""
    track_number: int = 0
    disc_number: int = 1
    duration: float = 0
    codec: str | None = None
    bitrate: int | None = None
    album_id: str | None = None
    source_sha: str | None = None
    source_revision: str | None = None
    cover_sha: str | None = None
    cover_url: str | None = None
    available: bool = True
    household_available: bool = False
    musicbrainz_release_id: str | None = None
    musicbrainz_recording_id: str | None = None
    isrcs: list[str] = Field(default_factory=list)
    genre: str | None = None
    server_state: ServerState = "in_library"
    request_progress: float | None = None
    # Mix playback keeps its private entry ID; personal actions use this file ID.
    library_track_id: str | None = None


class AlbumDTO(BaseModel):
    id: str
    title: str
    artist: str
    artist_id: str = ""
    year: str | int = ""
    category: str = "album"
    track_count: int = 0
    duration: float = 0
    cover_url: str | None = None
    cover_sha: str | None = None
    cover_colour: str | None = None
    musicbrainz_release_id: str | None = None
    edition: str | None = None
    codecs: list[str] = Field(default_factory=list)
    compilation: bool = False
    server_state: ServerState = "in_library"
    # When its newest song joined this person's library (UTC ISO 8601), for
    # "Recently added" (D-120, #281); none for an album of requested songs.
    added_at: str | None = None


class ArtistDTO(BaseModel):
    id: str
    name: str
    album_ids: list[str] = Field(default_factory=list)
    album_count: int = 0
    track_count: int = 0
    compilation_only: bool = False
    cover_album_id: str | None = None
    cover_url: str | None = None
    # The stored image behind cover_url (portrait or album cover), for
    # clients that address artwork by hash.
    cover_sha: str | None = None
    fallback_cover_url: str | None = None
    musicbrainz_artist_id: str | None = None


class AlbumDetail(AlbumDTO):
    tracks: list[TrackDTO]


class ArtistDetail(ArtistDTO):
    albums: list[AlbumDTO]
    compilation_tracks: list[TrackDTO] = Field(default_factory=list)
    # Every song you own on this artist's albums, album by album, for Play all.
    tracks: list[TrackDTO] = Field(default_factory=list)


class TrackPage(BaseModel):
    items: list[TrackDTO]
    total: int
    revision: str
    next_cursor: str | None


class AlbumPage(BaseModel):
    items: list[AlbumDTO]
    total: int
    revision: str
    next_cursor: str | None


class ArtistPage(BaseModel):
    items: list[ArtistDTO]
    total: int
    revision: str
    next_cursor: str | None


class PlaylistEntry(TrackDTO):
    entry_id: str
    position: int
    status: str
    match_kind: str | None = None
    saved_id: str | None = None
    household_available: bool = False
    saved_track: TrackDTO | None = None
    # The household file for a song not in this person's library, so it plays
    # from the server rather than a remote source.
    household_track: TrackDTO | None = None


class CancelledRequestDTO(BaseModel):
    cancelled: bool
    continues_for_others: bool


class PlaylistDTO(BaseModel):
    id: str
    name: str
    revision: int = 0
    favourites: bool = False
    track_count: int = 0
    available_count: int = 0
    review_count: int = 0
    entries: list[PlaylistEntry] = Field(default_factory=list)


class PlaylistList(BaseModel):
    playlists: list[PlaylistDTO]


class MixDTO(BaseModel):
    id: str
    kind: str
    day: str
    title: str
    reason: str
    tracks: list[TrackDTO]


class RadioCard(BaseModel):
    title: str
    ready: int


class MixList(BaseModel):
    mixes: list[MixDTO]
    preparing: bool
    radio: RadioCard | None = None


class RewindSong(BaseModel):
    track: TrackDTO
    plays: int


class RewindArtist(BaseModel):
    name: str
    plays: int
    minutes: int
    # The library artist page (`ArtistDTO.id`) when the artist is in the
    # person's library; else the catalogue page by MusicBrainz id, if known.
    id: str | None = None
    musicbrainz_artist_id: str | None = None
    # First played this period (as `new_artists`).
    new: bool = False
    # The cover of the artist's most played album this period.
    cover_url: str | None = None


class RewindAlbum(BaseModel):
    title: str
    artist: str
    plays: int
    cover_url: str | None = None
    # The library album page (`AlbumDTO.id`) when the album is in the
    # person's library; else the catalogue release page, if known.
    id: str | None = None
    musicbrainz_release_id: str | None = None


class RewindMix(BaseModel):
    title: str
    plays: int
    # The most played edition still kept, and its mix kind (`MixDTO.kind`).
    id: str | None = None
    kind: str | None = None


class RewindDay(BaseModel):
    """One local calendar day of the period, counted as the totals are."""

    start: int  # epoch seconds of the day's local midnight
    minutes: int
    plays: int


class RewindDTO(BaseModel):
    """A person's listening for the week, month or year so far (#203)."""

    period: Literal["week", "month", "year"]
    start: int
    end: int
    timezone: str
    plays: int
    minutes: int
    top_songs: list[RewindSong]
    top_artists: list[RewindArtist]
    top_albums: list[RewindAlbum]
    new_artists: list[str]
    top_mixes: list[RewindMix]
    # Each day from `start` up to and including today, in `timezone`.
    days: list[RewindDay] = Field(default_factory=list)


class RadioBatch(BaseModel):
    id: str
    title: str
    tracks: list[TrackDTO]
    preparing: bool


class SearchResults(BaseModel):
    tracks: list[TrackDTO]
    household_tracks: list[TrackDTO]
    artists: list[ArtistDTO]
    albums: list[AlbumDTO] = Field(default_factory=list)
    total_tracks: int = 0
    total_household_tracks: int = 0
    total_artists: int = 0
    total_albums: int = 0
    offset: int = 0
    next_offset: int | None = None
    revision: str = ""


class SearchRef(BaseModel):
    kind: Literal["song", "album", "artist", "playlist"]
    id: str


class FoundAlbum(AlbumDTO):
    in_library: bool = False


class FoundArtist(ArtistDTO):
    in_library: bool = False


class FoundResults(BaseModel):
    """One ranked search (D-61): Top results point into the typed lists."""

    top: list[SearchRef]
    songs: list[TrackDTO]
    albums: list[FoundAlbum]
    artists: list[FoundArtist]
    playlists: list[PlaylistDTO]
    # The person's Home mixes named like the search (#109).
    mixes: list[MixDTO] = Field(default_factory=list)


class Entities(BaseModel):
    playlists: list[PlaylistDTO] = Field(default_factory=list)
    tracks: list[TrackDTO] = Field(default_factory=list)
    albums: list[AlbumDTO] = Field(default_factory=list)
    artists: list[ArtistDTO] = Field(default_factory=list)


class DeletedEntities(BaseModel):
    playlists: list[str] = Field(default_factory=list)
    tracks: list[str] = Field(default_factory=list)
    albums: list[str] = Field(default_factory=list)
    artists: list[str] = Field(default_factory=list)


class LibraryChanges(BaseModel):
    revision: str
    upserts: Entities
    deletes: DeletedEntities


class APIError(BaseModel):
    detail: str
    code: str


class UserDTO(BaseModel):
    id: str
    username: str
    display_name: str
    role: Literal["owner", "member"]


class MeDTO(UserDTO):
    """The signed-in person and the public id of the session asking, so a
    web client can end exactly this session later (#80)."""

    session_id: str


class DeviceSessionDTO(BaseModel):
    user: UserDTO
    token: str
    expires_at: int


class InviteDTO(BaseModel):
    """A one-use setup code. The link carries it in the fragment, which browsers
    never send to the server, and is absent without a configured HTTPS origin."""

    user_id: str
    code: str
    expires_at: int
    invite_url: str | None


class ActivationDTO(BaseModel):
    user: UserDTO


class PlaybackURL(BaseModel):
    url: str
    expires_in: int


class PlaybackURLsRequest(BaseModel):
    track_ids: list[str] = Field(min_length=1, max_length=200)


class PlaybackURLs(BaseModel):
    # Signed playback addresses by song; songs without a file are left out.
    urls: dict[str, str]
    expires_in: int


class RequestDTO(BaseModel):
    id: str
    title: str
    artist: str = ""
    album: str = ""
    status: str
    received_bytes: int = 0
    total_bytes: int = 0
    server_state: ServerState
    request_progress: float | None = None
    error: str | None = None


class DownloadLogLineDTO(BaseModel):
    text: str
    ok: bool
    seconds: float | None = None


class DownloadLogDTO(BaseModel):
    """One download's story in plain words (#197); never names a peer."""

    id: str
    status: str
    lines: list[DownloadLogLineDTO]


class RequestList(BaseModel):
    total: int
    active: int
    offset: int
    next_offset: int | None
    library_revision: str
    downloads_enabled: bool
    items: list[RequestDTO]


class RequestGroupItemDTO(RequestDTO):
    track_number: int = 0
    disc_number: int = 1
    created_at: str | None = None
    updated_at: str | None = None
    # The source that supplied (or is supplying) the file, e.g. "soulseek".
    source: str | None = None
    # The file's quality in the shared vocabulary, e.g. "MP3 320 kbps".
    quality: str | None = None


class RequestGroupDTO(BaseModel):
    id: str
    kind: Literal["album", "song"]
    title: str
    artist: str = ""
    release_id: str | None = None
    state: Literal["active", "waiting", "paused", "done", "failed"]
    songs: int
    done: int
    active: int
    waiting: int
    failed: int
    # Songs a person paused (#228): neither active nor failed.
    paused: int
    progress: float
    requested_at: str
    updated_at: str
    items: list[RequestGroupItemDTO]


class RequestGroupList(BaseModel):
    total: int
    active: int
    offset: int
    next_offset: int | None
    downloads_enabled: bool
    groups: list[RequestGroupDTO]


class RecentAlbums(BaseModel):
    albums: list[AlbumDTO]
    revision: str


class HomeItemDTO(BaseModel):
    id: str
    name: str
    artist: str
    artist_id: str
    kind: Literal["artists", "albums", "songs"]
    release_id: str | None = None
    album: str = ""
    reason: str | None = None


class HomeFeedDTO(BaseModel):
    artists: list[HomeItemDTO]
    albums: list[HomeItemDTO]
    songs: list[HomeItemDTO]
    source: str
    unavailable: bool
    title: str | None = None
    state: Literal["ready", "no_seeds", "unavailable", "preparing"] | None = None


class CatalogueGroupDTO(BaseModel):
    id: str
    title: str
    date: str
    type: str
    # Your library album with this title by this artist, and how many of its
    # songs you have; null and 0 when you have none.
    saved_album_id: str | None = None
    saved_songs: int = 0


class CatalogueGroupsDTO(BaseModel):
    total: int
    offset: int
    releases: list[CatalogueGroupDTO]


class CatalogueEditionDTO(BaseModel):
    id: str
    title: str
    date: str
    country: str
    description: str
    status: str
    formats: list[str]
    track_count: int


class CatalogueEditionsDTO(BaseModel):
    total: int
    offset: int
    editions: list[CatalogueEditionDTO]


class CatalogueTrackDTO(TrackDTO):
    id: str
    # True on an edition other than the recommended one: a request keeps it.
    explicit_edition: bool = False
    # Other recordings than the studio ones (a Live, Remix or Demo release).
    recording_kind: list[str] = Field(default_factory=list)
    # Only populated for a file in the caller's personal library.
    saved_track: TrackDTO | None = None
    household_track: TrackDTO | None = None


class CatalogueReleaseDTO(BaseModel):
    id: str
    title: str
    artist: str
    # MusicBrainz ID of the first credited artist; empty when the credit has none.
    artist_id: str = ""
    tracks: list[CatalogueTrackDTO]


class CatalogueArtistDTO(BaseModel):
    id: str
    name: str
    description: str


class CatalogueArtistsDTO(BaseModel):
    artists: list[CatalogueArtistDTO]
    total: int
    offset: int
    next_offset: int | None


class FavouriteResultDTO(BaseModel):
    favourited: bool


class MembershipResultDTO(BaseModel):
    added: int
    revision: str


class AcquisitionResultDTO(BaseModel):
    created: int
    existing: int
    skipped: int
    # Songs the person paused themselves: Add leaves them paused; Resume in
    # Downloads resumes them (#264).
    paused: int = 0
    # Those paused requests' IDs, one per song left paused: Resume in
    # Downloads names the request, so the iPhone clears that song's mark by
    # it, never by title (#276).
    paused_ids: list[str] = []
    # One per song asked for, in order: created (a new request), existing
    # (already being added), added (the household's copy, now in your
    # library), paused (your own pause, left so), excluded (left out of the
    # household's downloads), in_library (already yours) or over_limit (left
    # out by a queue limit, D-128: nothing was written, so ask for it again
    # later). Lets a client mark each song without asking again (Codex
    # round 1 on batch K).
    outcomes: list[
        Literal[
            "created",
            "existing",
            "added",
            "paused",
            "excluded",
            "in_library",
            "over_limit",
        ]
    ] = []
    # Songs a queue limit left out (D-128), which limit (person_waiting,
    # household_waiting or rate) and, for the rate, the seconds until one
    # more song fits. When every song sent was left out the answer is 429
    # with the code queue_limit and these same fields, so a client that
    # keeps a busy request to send again keeps these songs too.
    left_out: int = 0
    limit: Literal["person_waiting", "household_waiting", "rate"] | None = None
    retry_after: int | None = None
    downloads_enabled: bool


class ClearedRequestsDTO(BaseModel):
    cleared: int


class DeletedPlaylistDTO(BaseModel):
    deleted: bool


class ImportResultDTO(BaseModel):
    playlists: int
    tracks: int


class AppleMusicImportCounts(BaseModel):
    in_library: int
    household: int
    missing: int
    # Missing songs sent to Downloads (only when the person asked).
    requested: int
    # Missing songs a queue limit left out (D-128): asked for again later.
    left_out: int = 0


class AppleMusicImportDTO(BaseModel):
    playlist: PlaylistDTO
    counts: AppleMusicImportCounts


class SessionDTO(BaseModel):
    id: str
    kind: str
    device_name: str
    platform: str
    created_at: float
    last_seen_at: float
    expires_at: float
    revoked_at: float | None
    # The session making this request ("This device").
    current: bool = False


class SessionsDTO(BaseModel):
    sessions: list[SessionDTO]


class PersonDTO(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    disabled: int = 0
    # Songs in this person's library.
    song_count: int = 0
    # The newest last use of any of their sessions (ISO 8601), if any.
    last_seen: str | None = None


class PeopleDTO(BaseModel):
    people: list[PersonDTO]


class RevokedDTO(BaseModel):
    revoked: bool


class RevokedOthersDTO(BaseModel):
    # How many other sessions were signed out.
    revoked: int


class PasswordChangedDTO(BaseModel):
    changed: bool


class QualityOptionDTO(BaseModel):
    value: str
    label: str
    rank: float


class QualityItemDTO(BaseModel):
    scope: str
    item_id: str
    preferred: str


class QualityDTO(BaseModel):
    preferences: list[QualityItemDTO]
    options: list[QualityOptionDTO]
    household_default: str


class QualitySavedDTO(BaseModel):
    scope: str
    item_id: str
    preferred: str | None
    created: int


class RemovalItemDTO(BaseModel):
    track_id: str
    delete_file: bool
    kept_for: list[str]


class RemovalPreviewDTO(BaseModel):
    delete_count: int
    keep_count: int
    items: list[RemovalItemDTO]
    confirmation: str


class RemovalResultDTO(BaseModel):
    id: str
    removed: int
    deleted: int
    kept: int
    undo_until: float


class RestoredDTO(BaseModel):
    restored: int


class RecordedDTO(BaseModel):
    recorded: int


class ImportPreviewDTO(BaseModel):
    playlists: int
    tracks: int
    saved: int
    household: int
    missing: int


class ArtistAcquisitionDTO(BaseModel):
    status: str
    monitor: bool
    kinds: list[str]
    total: int = 0
    done: list[str] = Field(default_factory=list)
    created: int = 0
    error: str | None = None


class RetriedDTO(BaseModel):
    retried: bool


class PausedDTO(BaseModel):
    paused: bool


class ResumedDTO(BaseModel):
    resumed: bool


class CataloguePopularSongDTO(BaseModel):
    id: str
    title: str
    artist: str
    album: str
    release_id: str


class CataloguePopularityDTO(BaseModel):
    enabled: bool
    songs: list[CataloguePopularSongDTO] = Field(default_factory=list)
    albums: list[CatalogueGroupDTO] = Field(default_factory=list)
    ranked_album_ids: list[str] = Field(default_factory=list)


class CatalogueProfileDTO(BaseModel):
    id: str
    name: str
    description: str = ""
    country: str = ""
    type: str = ""
    begin: str = ""
    end: str = ""


class ArtistIdentityDTO(BaseModel):
    id: str
    name: str


class LookupMatchDTO(BaseModel):
    track: TrackDTO | None = None


class LookupResultDTO(BaseModel):
    matches: list[LookupMatchDTO]


class ServerInfoDTO(BaseModel):
    version: str
    revision: str
    # Raised when apps gain a server feature they should check for.
    api_level: int
    capabilities: list[str]
