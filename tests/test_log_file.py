"""#148 (D-75): the server keeps its log in a rotating file in its data folder,
so a container restart no longer loses it. Runs in a child process: applying a
logging config would otherwise change logging for later tests."""

import subprocess
import sys
import textwrap
from pathlib import Path

import server

# The folder that holds the server package: in the image, tests/ and server/
# are not siblings, so the child process starts where `server` imports.
ROOT = Path(server.__file__).resolve().parents[1]


def run(data, script):
    done = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), str(data)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return done


def test_the_log_is_written_to_a_rotating_file_without_secrets(tmp_path):
    run(
        tmp_path,
        """
        import logging, logging.config, json, sys
        from pathlib import Path
        import server.runtime as runtime
        runtime.LOG_FILE_BYTES = 4000
        path = runtime.log_config(Path(sys.argv[1]))
        logging.config.dictConfig(json.loads(path.read_text()))
        logging.getLogger("pig_music.downloads").info("first line token=abc123")
        access = logging.getLogger("uvicorn.access")
        access.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4", "GET", "/api/health", "1.1", 200)
        access.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4", "GET", "/api/v1/me", "1.1", 200)
        for i in range(200):
            logging.getLogger("pig_music.home").warning("line %d", i)
        """,
    )
    logs = tmp_path / "logs"
    current, older = logs / "pig-music.log", logs / "pig-music.log.1"
    assert current.exists() and older.exists()
    # Rotated at the limit: one backup, so the files stay bounded.
    assert not (logs / "pig-music.log.2").exists()
    assert current.stat().st_size <= 4000 and older.stat().st_size <= 4000
    text = older.read_text() + current.read_text()
    assert "line 199" in text
    # The same filters as the console log.
    assert "abc123" not in text
    assert "/api/health" not in text


def test_a_restart_keeps_the_earlier_log(tmp_path):
    script = """
        import logging, logging.config, json, sys
        from pathlib import Path
        import server.runtime as runtime
        path = runtime.log_config(Path(sys.argv[1]))
        logging.config.dictConfig(json.loads(path.read_text()))
        logging.getLogger("pig_music").warning("run %s", sys.argv[1][-3:])
        """
    run(tmp_path, script)
    run(tmp_path, script)
    assert (tmp_path / "logs/pig-music.log").read_text().count(
        "WARNING pig_music: run"
    ) == 2


def test_a_log_folder_that_cannot_be_written_falls_back_to_the_console(
    tmp_path, capsys
):
    """Codex on 6212de1: an optional log file must never stop the server."""
    import server.runtime as runtime

    packaged = Path(runtime.__file__).with_name("logging.json")
    # "logs" is a file, so the folder can't be made.
    blocked = tmp_path / "a"
    blocked.mkdir()
    (blocked / "logs").write_text("")
    assert runtime.log_config(blocked) == packaged
    # The folder exists but the log file can't be opened (it is a folder).
    unopenable = tmp_path / "b"
    (unopenable / "logs/pig-music.log").mkdir(parents=True)
    assert runtime.log_config(unopenable) == packaged
    assert "log file" in capsys.readouterr().err
