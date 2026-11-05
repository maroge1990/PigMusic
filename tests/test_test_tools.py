"""Test tools stay out of the production image; the suite still runs in it (#86)."""

import importlib.metadata
import importlib.util
import inspect
import os
import re
import shlex
import subprocess
import sys

import pytest

from source import SOURCE

TOOLS = {"pytest", "pytest-xdist", "pluggy", "iniconfig", "execnet", "pygments"}


def locked(name):
    names = set()
    for line in (SOURCE / name).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(
                re.split(r"[=<>!~\[; ]", line, maxsplit=1)[0].lower().replace("_", "-")
            )
    return names


def deploy_unraid():
    spec = importlib.util.spec_from_file_location(
        "deploy_unraid", SOURCE / "scripts/deploy_unraid.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_lock_has_no_test_tools_and_the_test_lock_has_them():
    runtime, tools = locked("requirements.lock.txt"), locked(
        "requirements-test.lock.txt"
    )
    assert not runtime & TOOLS
    assert TOOLS <= tools
    # --no-deps into a separate folder: it must never shadow a runtime package.
    assert not runtime & tools
    assert (
        "-r requirements-test.lock.txt"
        in (SOURCE / "requirements-dev.txt").read_text().splitlines()
    )


def test_the_test_lock_is_closed_over_pytest_and_xdist():
    from packaging.requirements import Requirement

    tools = locked("requirements-test.lock.txt")
    for dist in ("pytest", "pytest-xdist"):
        for spec in importlib.metadata.requires(dist) or []:
            requirement = Requirement(spec)
            if requirement.marker and not requirement.marker.evaluate(
                {"extra": "", "sys_platform": "linux"}
            ):
                continue
            assert requirement.name.lower() in tools, (dist, spec)


def test_the_suite_inside_the_image_loads_pytest_from_the_test_folder():
    if not os.environ.get("PIG_MUSIC_SOURCE"):
        pytest.skip("Runs only inside the image (CI and the release verify)")
    assert pytest.__file__.startswith("/opt/pig-test/"), pytest.__file__


def test_the_dockerfile_refuses_test_tools():
    path = SOURCE / "Dockerfile"
    if not path.is_file():
        pytest.skip("Dockerfile is not part of this checkout")
    final = path.read_text().rsplit("\nFROM ", 1)[1]
    install = final.index("pip install --no-cache-dir -r requirements.lock.txt")
    check = final.index("u.find_spec(m)")
    assert install < check
    assert not re.search(r"^(COPY|RUN).*requirements-test", final, re.M)


def test_no_test_tools_check_fails_when_pytest_is_importable():
    snippet = deploy_unraid().NO_TEST_TOOLS
    found = subprocess.run([sys.executable, "-c", snippet], capture_output=True)
    assert found.returncode == 1 and b"pytest" in found.stderr
    # Without site-packages or PYTHONPATH (as in the clean image) it passes.
    clean = subprocess.run([sys.executable, "-I", "-S", "-c", snippet])
    assert clean.returncode == 0


def test_release_verify_checks_the_image_then_runs_the_suite_with_the_test_folder():
    module = deploy_unraid()
    image = "ghcr.io/maroge1990/pig-music@sha256:" + "a" * 64
    stage = module.REMOTE + "/releases/v1-test"
    check, install, suite = module.suite_commands(image, stage)

    assert "--network none" in check and shlex.quote(image) in check
    assert shlex.quote(module.NO_TEST_TOOLS) in check

    # The install adds only the locked tools, outside the image's site-packages.
    assert shlex.quote(image) in install
    for flag in ("--no-deps", "--only-binary :all:", "--target /opt/pig-test"):
        assert flag in install
    assert stage + "/requirements-test.lock.txt:" in install
    assert "--network none" not in install

    # The suite itself still has no network and the exact image.
    assert suite.startswith("docker run --rm --network none --memory=1g --user 99:100")
    assert shlex.quote(stage + "/testlibs:/opt/pig-test:ro") in suite
    assert "-e PYTHONPATH=/opt/pig-test " in suite
    assert (shlex.quote(image) + " -m pytest /tests") in suite
    # The stage on the host gets the test lock from the source bundle.
    assert '"requirements-test.lock.txt",' in inspect.getsource(module.bundle)


def test_ci_checks_the_image_and_mounts_the_test_folder_for_every_suite_run():
    path = SOURCE / ".github/workflows/container.yml"
    if not path.is_file():
        pytest.skip("Workflows are not part of this checkout")
    workflow = path.read_text()
    assert (
        "pip-audit -r requirements.lock.txt -r requirements-test.lock.txt" in workflow
    )
    check = workflow.index("name: No test tools in the production image")
    install = workflow.index("name: Test tools for the suite, outside the image")
    assert check < install < workflow.index("-m pytest /tests")
    assert "u.find_spec(m)" in workflow[check:install]
    assert "--no-deps" in workflow[install:] and "--target /opt/pig-test" in workflow
    runs = workflow.split("docker run ")[1:]
    suites = [run for run in runs if "-m pytest /tests" in run]
    assert len(suites) == 2
    for run in suites:
        assert run.startswith("--rm --network none")
        assert '"$RUNNER_TEMP/pig-testlibs:/opt/pig-test:ro"' in run
        assert "-e PYTHONPATH=/opt/pig-test" in run
