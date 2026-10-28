"""Import every module in the mas_optimiser package.

A module that nothing else imports is invisible to the rest of the suite, so a
rename or a moved symbol can leave it importing something that no longer exists
without any test failing. Walking the package and importing each module turns
that class of breakage into a test failure.
"""

import importlib
import pkgutil
import re
import subprocess
from pathlib import Path

import pytest

import mas_optimiser


def test_all_modules_import():
    for module in pkgutil.walk_packages(
        mas_optimiser.__path__, prefix="mas_optimiser."
    ):
        if module.name.rsplit(".", 1)[-1] == "__main__":
            continue
        importlib.import_module(module.name)


def test_version_is_set():
    assert isinstance(mas_optimiser.__version__, str)
    assert mas_optimiser.__version__


def test_code_version_names_the_checked_out_commit_from_any_directory(
    tmp_path, monkeypatch
):
    described = subprocess.run(
        ["git", "describe", "--tags", "--long", "--dirty", "--always"],
        cwd=Path(mas_optimiser.__file__).parent,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.chdir(tmp_path)
    assert mas_optimiser.code_version() == described
    # A tag where the history has one, else a bare commit (a shallow clone).
    assert re.fullmatch(r"(v\d+\.\d+\.\d+-\d+-g)?[0-9a-f]{7,}(-dirty)?", described)


@pytest.mark.parametrize(
    "failure",
    [OSError("git not found"), subprocess.CalledProcessError(128, "git")],
)
def test_code_version_falls_back_to_the_installed_version_outside_git(
    monkeypatch, failure
):
    def no_git(*args, **kwargs):
        assert kwargs.get("check"), "a failed describe must raise"
        raise failure

    monkeypatch.setattr(mas_optimiser.subprocess, "run", no_git)
    assert mas_optimiser.code_version() == mas_optimiser.__version__
