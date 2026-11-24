"""Sphinx configuration for the mas-optimiser documentation."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

project = "mas-optimiser"
author = "Cameron McCormack"
try:
    release = package_version("mas-optimiser")
except PackageNotFoundError:
    release = "0.0.0"
version = ".".join(release.split(".")[:2])

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

myst_enable_extensions = ["dollarmath", "colon_fence"]
myst_heading_anchors = 3

autosummary_generate = True
autodoc_member_order = "bysource"
autodoc_typehints = "description"
napoleon_google_docstring = True
napoleon_numpy_docstring = False
# Attributes sections become field lists, so tuple fields are not described twice.
napoleon_use_ivar = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable", None),
    "scipy": ("https://docs.scipy.org/doc/scipy", None),
}

exclude_patterns = [
    "_build",
    "figures/gate/comparison.md",
    "figures/calibration/calibration.md",
]
html_theme = "sphinx_rtd_theme"
# The planner, a page of its own that needs nothing but its file.
html_static_path = ["_static"]
