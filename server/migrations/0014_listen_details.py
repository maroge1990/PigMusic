"""What each listen was, kept with the listen (#203): Rewind ranks a year of
listening, but mix editions are deleted a month after they retire. Filled
in for earlier mix listens whose edition is still kept."""

import json

COLUMNS = "title,artist,album,album_artist,release_id,duration,mix_title"


def up(conn):
    conn.execute(
        """CREATE TABLE listen_details (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            event_id TEXT NOT NULL,
            title TEXT NOT NULL, artist TEXT NOT NULL, album TEXT, album_artist TEXT,
            release_id TEXT, duration REAL, mix_title TEXT,
            PRIMARY KEY(user_id,event_id))"""
    )
    editions = {}
    for user, event, track_id in conn.execute(
        "SELECT user_id,event_id,track_id FROM play_events WHERE track_id LIKE 'mix:%'"
    ).fetchall():
        try:
            _, identity, index = track_id.split(":")
            index = int(index)
        except ValueError:
            continue
        if identity not in editions:
            row = conn.execute(
                "SELECT payload FROM mix_editions WHERE id=? AND user_id=?",
                (identity, user),
            ).fetchone()
            editions[identity] = json.loads(row[0]) if row else None
        payload = editions[identity]
        if not payload or not 0 <= index < len(payload.get("tracks", [])):
            continue
        track = payload["tracks"][index]
        conn.execute(
            f"INSERT OR IGNORE INTO listen_details(user_id,event_id,{COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)",
            (user, event, *details(track, payload.get("title"))),
        )


def details(track, mix_title=None):
    """The listen-detail columns for `track`, in COLUMNS order."""
    return (
        track.get("title") or "",
        track.get("artist") or "",
        track.get("album"),
        track.get("album_artist"),
        track.get("musicbrainz_release_id"),
        track.get("duration"),
        mix_title,
    )
