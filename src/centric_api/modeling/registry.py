from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from ..config import ConfigError, runtime_home
from .contracts import ModelDefinition, ModelProtocol

PRIVATE_MODELS_DIR = Path("models")


def private_models_dir(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    return runtime_home() / PRIVATE_MODELS_DIR


def discover_models(path: str | Path | None = None) -> tuple[ModelProtocol, ...]:
    directory = private_models_dir(path)
    if not directory.is_dir():
        return ()
    models: list[ModelProtocol] = []
    for model_path in sorted(directory.glob("*.py")):
        if model_path.name.startswith("_"):
            continue
        models.append(_load_model(model_path))
    _ensure_unique_models(models)
    return tuple(models)


def select_model(models: tuple[ModelProtocol, ...], name: str) -> ModelProtocol:
    for model in models:
        if model.definition.name == name:
            return model
    names = ", ".join(model.definition.name for model in models)
    if names:
        raise ConfigError(f"Unknown model {name!r}. Available: {names}")
    raise ConfigError("No models found.")


def _load_model(path: Path) -> ModelProtocol:
    module_name = f"centric_api_private_model_{path.stem}_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Could not load model file: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise ConfigError(f"Could not import model file {path}: {exc}") from exc
    model = _module_model(module)
    _validate_model(model, path)
    return model


def _module_model(module: Any) -> Any:
    if hasattr(module, "get_model"):
        return module.get_model()
    if hasattr(module, "MODEL"):
        return module.MODEL
    raise ConfigError("Model file must expose MODEL or get_model().")


def _validate_model(model: Any, path: Path) -> None:
    definition = getattr(model, "definition", None)
    if not isinstance(definition, ModelDefinition):
        raise ConfigError(f"Model {path} must expose a ModelDefinition as definition.")
    if not definition.name.strip():
        raise ConfigError(f"Model {path} has an empty name.")
    if not definition.output_table.strip():
        raise ConfigError(f"Model {definition.name!r} has an empty output table.")
    for method in ("check", "run"):
        if not callable(getattr(model, method, None)):
            raise ConfigError(f"Model {definition.name!r} must define {method}().")


def _ensure_unique_models(models: list[ModelProtocol]) -> None:
    seen: set[str] = set()
    for model in models:
        name = model.definition.name
        if name in seen:
            raise ConfigError(f"Duplicate model name: {name}")
        seen.add(name)
