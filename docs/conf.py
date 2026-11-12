# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — Sphinx documentation configuration              ║
# ║  « MyST + autodoc + Napoleon + Furo »                            ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  The user manual (manual/user_manual.md) is the same file the    ║
# ║  in-app Help window and the PDF use; the site adds the API.      ║
# ╚══════════════════════════════════════════════════════════════════╝
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# -- Project information -----------------------------------------------
project = "Rig Conductor"
author = "Bart R. H. Geurten"
copyright = "2025–2026, Bart R. H. Geurten"  # noqa: A001

from rigconductor import __version__  # noqa: E402

release = __version__
version = __version__

# -- General configuration ---------------------------------------------
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "myst_parser",
    "sphinx_copybutton",
]
exclude_patterns = ["_build", "manual/*.pdf"]

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_use_param = True
napoleon_use_rtype = True

autodoc_member_order = "bysource"
autodoc_typehints = "description"
# SLEAP and the analysis backends are not needed to read the docstrings (PyQt6 is
# installed: mocking it breaks the enum flags the dialogs use as defaults).
autodoc_mock_imports = [
    "sleap_io",
    "thermokourt",
    "tadpose",
    "h5py",
    "imageio",
]

source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
myst_heading_anchors = 3  # "#1-setup" links in the manual work on the site too
suppress_warnings = ["myst.header"]  # the manual starts at "#", the site adds its own nav

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}

# -- HTML output -------------------------------------------------------
html_theme = "furo"
html_title = f"Rig Conductor {version}"
html_static_path: list[str] = []
html_theme_options = {
    "source_repository": "https://github.com/zerotonin/RigConductor",
    "source_branch": "main",
    "source_directory": "docs/",
}
