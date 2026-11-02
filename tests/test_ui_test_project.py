"""The simulator UI tests: their Xcode project must match its generator, so a
test file added under swift/PigMusicUITests is never left out of the build,
and their test server's certificates must pass strict checks."""

import importlib.util
import shutil
import ssl
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate_ui_test_project.py"
SERVER = ROOT / "scripts/serve_ui_test_server.py"


@pytest.mark.skipif(
    not GENERATOR.exists(), reason="native project sources are not in this image"
)
def test_the_ui_test_project_matches_its_generator():
    spec = importlib.util.spec_from_file_location("generate_ui_test_project", GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    assert generator.main(["--check"]) == 0


@pytest.mark.skipif(
    not SERVER.exists() or shutil.which("openssl") is None,
    reason="needs the simulator test server's script and openssl",
)
def test_the_ui_test_servers_certificate_passes_strict_checks(tmp_path):
    """iOS and Python 3.13+ reject a CA without key usages; the first
    certificates made with `req -x509` failed the runner's health check."""
    spec = importlib.util.spec_from_file_location("serve_ui_test_server", SERVER)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    _, cert = server.certificates(tmp_path)
    result = subprocess.run(
        ["openssl", "verify", "-x509_strict", "-CAfile", str(tmp_path / "ca.pem")]
        + [str(cert)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    strict = ssl.create_default_context(cafile=str(tmp_path / "ca.pem"))
    assert strict.verify_flags & ssl.VERIFY_X509_STRICT
