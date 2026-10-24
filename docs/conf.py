# Sphinx configuration for the neurobank documentation
import nbank

project = "neurobank"
author = "Dan Meliza"
copyright = "2013-2026, Dan Meliza"
release = nbank.__version__
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinxarg.ext",
]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
exclude_patterns = ["_build", "examples.md"]
# docstrings use `name` for code
default_role = "code"

autodoc_member_order = "bysource"
autodoc_typehints = "signature"
# one parameter per line for long signatures
python_maximum_signature_line_length = 88
autodoc_default_options = {"members": True, "show-inheritance": False}

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

html_theme = "furo"
html_title = f"neurobank {release}"
