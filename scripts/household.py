"""Household network values for the live tools, kept out of Git.

Values come from the process environment, else from the gitignored
``.private/household.env`` (plain ``KEY=value`` lines). Keys and placeholders
are listed in ``.env.example``. Nothing is read at import time: tests and
offline tools import the live scripts without ``.private/``.
"""

import os
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / ".private/household.env"


class HouseholdError(RuntimeError):
    pass


def read(path=None):
    """Parse ``KEY=value`` lines; blank lines and ``#`` comments are ignored."""
    path = Path(path) if path else FILE
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def get(key, path=None):
    """Return a household value, or raise naming the key and where to set it."""
    value = os.environ.get(key, "").strip() or read(path).get(key, "")
    if not value or "<" in value or ">" in value:
        raise HouseholdError(
            f"{key} is not set. Add the real value to {path or FILE} "
            f"(see .env.example) or export it in the environment."
        )
    return value
