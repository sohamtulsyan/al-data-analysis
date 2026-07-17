"""Import script modules without installing scripts as a package."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from retention_pipeline.config import PROJECT_ROOT

_LOADED: dict[str, ModuleType] = {}


def load_script_module(filename: str) -> ModuleType:
    if filename in _LOADED:
        return _LOADED[filename]
    path = PROJECT_ROOT / "scripts" / filename
    if not path.is_file():
        raise FileNotFoundError(f"Script not found: {path}")
    name = f"retention_scripts_{filename.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    _LOADED[filename] = mod
    return mod
