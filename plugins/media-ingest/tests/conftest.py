"""Import the plugin directory as a real package under a stable alias.

Loading `__init__.py` as a loose module breaks its relative import
(`from .tools import ...`). Registering the directory with
`submodule_search_locations` makes it a package, which mirrors how the Hermes
loader imports it.
"""
import importlib.util
import pathlib
import sys

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]
ALIAS = 'media_ingest_plugin'

if ALIAS not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        ALIAS, PLUGIN_DIR / '__init__.py',
        submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[ALIAS] = module
    spec.loader.exec_module(module)
