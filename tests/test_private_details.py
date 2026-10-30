"""Household network details stay out of every tracked file but the archive.

The real values live only in the gitignored .private/household.env (see
.env.example). Without that file (CI, the release image) this check skips.
"""

import ipaddress
import subprocess
from urllib.parse import urlsplit

import pytest

from source import SOURCE

HOUSEHOLD = SOURCE / ".private/household.env"
ARCHIVE = "docs/archive/"


def needles(values):
    """Each value, plus the hosts, parent domains and LAN subnets inside it."""
    found = set()
    for value in values:
        found.add(value.lower())
        host = urlsplit(value).hostname if "://" in value else value
        host = (host or "").rpartition("@")[2].lower()
        if not host:
            continue
        found.add(host)
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if len(labels) >= 3:
                found.add(".".join(labels[1:]))
            continue
        if address.version == 4 and address.is_private:
            found.add(host.rsplit(".", 1)[0] + ".")
    return {n for n in found if len(n) >= 6}


def test_household_values_appear_only_in_the_archive():
    if not HOUSEHOLD.is_file():
        pytest.skip("No .private/household.env in this checkout")
    values = []
    for line in HOUSEHOLD.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            value = line.split("=", 1)[1].strip().strip("\"'")
            if value:
                values.append(value)
    search = needles(values)
    assert search
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=SOURCE,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    leaks = []
    for name in tracked:
        path = SOURCE / name
        if not name or name.startswith(ARCHIVE) or not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", "ignore").lower()
        leaks.extend(f"{name}: {n}" for n in sorted(search) if n in text)
    assert not leaks, "Household details in tracked files:\n" + "\n".join(leaks)
