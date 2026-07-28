# Configuration file for the Sphinx documentation builder.

import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(".."))

# -- Project information -----------------------------------------------------

iYear = datetime.date.today().year

project = "Vaibify"
copyright = f"2025-{iYear}, Rory Barnes"
author = "Rory Barnes"

try:
    from vaibify import __version__
    release = __version__
except ImportError:
    release = "0.1"

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# Without this, myst generates no heading anchors at all, so every
# `[text](otherPage.md#a-heading)` link in the docs silently resolves
# to nothing. The docs are full of them; they accounted for most of
# the 28 warnings that surfaced when the build was first run with -W.
myst_heading_anchors = 4

master_doc = "index"

templates_path = ["_templates"]

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

pygments_style = "sphinx"

# -- Options for HTML output -------------------------------------------------

html_theme = "sphinx_rtd_theme"

html_theme_options = {
    "logo_only": False,
    "display_version": True,
}

html_static_path = ["_static"]
