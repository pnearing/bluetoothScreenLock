# Configuration file for the Sphinx documentation builder.
#
# Build locally with:
#   pip install sphinx myst-parser sphinx-autodoc-typehints
#   sphinx-build -b html docs docs/_build/html

import os
import sys
from datetime import datetime

# Ensure the package is importable (src layout)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# Project information
project = 'Bluetooth Screen Lock'
try:
    from bluetooth_screen_lock import __version__ as release
except Exception:
    release = '0.0.0'

copyright = f"{datetime.now().year}, Bluetooth Screen Lock"
author = 'Bluetooth Screen Lock Maintainers'

# General configuration
extensions = [
    'myst_parser',
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.autosummary',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinx_autodoc_typehints',
]

# Napoleon (Google/NumPy) docstrings
napoleon_google_docstring = True
napoleon_numpy_docstring = False

# Autosummary
autosummary_generate = True

# MyST (Markdown) settings
myst_enable_extensions = [
    'fieldlist',
    'deflist',
]

# Intersphinx mappings (optional but useful)
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', {}),
}

# Templates path
templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# HTML output
html_theme = 'alabaster'
html_static_path = ['_static']
