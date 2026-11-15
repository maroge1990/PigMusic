"""The suite's own isolation from the developer's data and from itself."""

import os
from pathlib import Path


def test_the_app_built_at_import_has_a_data_folder_per_test_process():
    # #189: every xdist worker imported server.app with the same
    # PIG_MUSIC_DATA and created one database at once ("database is locked"
    # at collection). conftest gives each process its own folder first.
    from server.app import app

    directory = app.state.store.directory
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    assert directory.name.startswith(f"pig-music-data-{worker}-")
    assert directory == Path(os.environ["PIG_MUSIC_DATA"])
