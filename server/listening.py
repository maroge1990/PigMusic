"""What a listen was, kept with it (#203): see migration 0014."""

COLUMNS = "title,artist,album,album_artist,release_id,duration,mix_title"


def details(track, mix_title=None):
    """The listen_details columns for `track`, in COLUMNS order."""
    return (
        track.get("title") or "",
        track.get("artist") or "",
        track.get("album"),
        track.get("album_artist"),
        track.get("musicbrainz_release_id"),
        track.get("duration"),
        mix_title,
    )
