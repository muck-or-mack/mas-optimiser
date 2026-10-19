"""Import every module in the mas_optimiser package.

A module that nothing else imports is invisible to the rest of the suite, so a
rename or a moved symbol can leave it importing something that no longer exists
without any test failing. Walking the package and importing each module turns
that class of breakage into a test failure.
"""

import importlib
import pkgutil

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
