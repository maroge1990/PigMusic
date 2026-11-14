"""`python -m server.admin` (D-93): the way back in when the owner password is lost."""

import sys

import pytest

from server import admin
from server.auth import Auth, AuthError
from server.store import Store

PASSWORD = "correct horse battery staple"
NEW = "another long passphrase here"


def run(monkeypatch, tmp_path, argv, answers, tty=True):
    monkeypatch.setenv("PIG_MUSIC_DATA", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["server.admin", *argv])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
    replies = iter(answers)
    monkeypatch.setattr(admin, "getpass", lambda prompt: next(replies))
    admin.main()


def test_create_owner_then_reset_password_from_the_terminal(
    monkeypatch, tmp_path, capsys
):
    run(monkeypatch, tmp_path, ["create-owner", "--username", "mark"], [PASSWORD] * 2)
    auth = Auth(Store(tmp_path))
    session = auth.login("mark", PASSWORD, "web", "Browser", "web", "127.0.0.1")
    run(monkeypatch, tmp_path, ["reset-password", "--username", "mark"], [NEW] * 2)
    out = capsys.readouterr().out
    assert "Account updated" in out and PASSWORD not in out and NEW not in out
    auth = Auth(Store(tmp_path))
    assert auth.login("mark", NEW, "web", "Browser", "web", "127.0.0.1")
    # The reset signs out every earlier session.
    with pytest.raises(AuthError):
        auth.authenticate(session["token"])


def test_refuses_piped_passwords_and_mismatches(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as piped:
        run(
            monkeypatch, tmp_path, ["create-owner", "--username", "mark"], [], tty=False
        )
    assert piped.value.code == 1
    with pytest.raises(SystemExit) as mismatch:
        run(
            monkeypatch,
            tmp_path,
            ["create-owner", "--username", "mark"],
            [PASSWORD, PASSWORD + "x"],
        )
    assert mismatch.value.code == 1
