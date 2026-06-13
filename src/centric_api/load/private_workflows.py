from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

from ..config import ConfigError, runtime_home

PRIVATE_WORKFLOW_DIR = Path("load/workflows")


def private_workflow_function(workflow: str, function_name: str) -> Callable[..., Any]:
    path = _private_workflow_path(workflow)
    module = _load_private_workflow_module(workflow, str(path))
    function = getattr(module, function_name, None)
    if not callable(function):
        path = _private_workflow_path(workflow)
        raise ConfigError(f"Private load workflow {path} is missing callable {function_name}().")
    return function


@cache
def _load_private_workflow_module(workflow: str, path_text: str) -> ModuleType:
    path = Path(path_text)
    if not path.is_file():
        raise ConfigError(
            f"Unknown load workflow {workflow!r}. Built-in workflows are public; "
            f"private workflows must be defined at {path}."
        )
    module_name = f"centric_api_private_load_workflow_{workflow}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Could not load private load workflow: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise ConfigError(f"Private load workflow failed to import {path}: {exc}") from exc
    return module


def _private_workflow_path(workflow: str) -> Path:
    return runtime_home() / PRIVATE_WORKFLOW_DIR / f"{workflow}.py"
