"""The library artwork preloader's idle cost (#63)."""

import json
import sys
import threading

from fastapi.testclient import TestClient

import server.app
from server.app import create_app


def test_preloader_reads_media_settings_once_per_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("PIG_MUSIC_PRELOAD", "true")
    tracks = [
        {
            "id": f"t{i}",
            "title": f"Song {i}",
            "artist": f"Artist {i}",
            "album_artist": f"Artist {i}",
            "album": f"Album {i}",
            "relative_path": f"song{i}.mp3",
        }
        for i in range(20)
    ]
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
    reads, passed = [], threading.Event()
    real = server.app.public_settings

    def counted(store):
        reads.append(sys._getframe(1).f_code.co_name)
        return real(store)

    passes = []

    async def next_pass(seconds):
        passes.append(seconds)
        if len(passes) < 50:  # Long enough for every item to be seen.
            return await sleep(0)
        passed.set()
        await server.app.asyncio.Event().wait()  # Until shutdown cancels it.

    sleep = server.app.asyncio.sleep
    monkeypatch.setattr(server.app, "public_settings", counted)
    monkeypatch.setattr(
        server.app.asyncio,
        "sleep",
        lambda seconds: next_pass(seconds) if seconds == 15 else sleep(seconds),
    )
    app = create_app(tmp_path)
    with TestClient(app):
        assert passed.wait(20)
        assert len(app.state.store.library["albums"]) == 20
    # 20 albums and 20 artists: one read per pass, not one per item seen.
    assert reads.count("preload_library") == len(passes), reads
