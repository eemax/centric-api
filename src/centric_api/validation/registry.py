from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from ..config import ConfigError, runtime_home
from .contracts import ValidationDefinition, ValidatorProtocol

PRIVATE_VALIDATORS_DIR = Path("validators")


def private_validators_dir(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    return runtime_home() / PRIVATE_VALIDATORS_DIR


def discover_validators(path: str | Path | None = None) -> tuple[ValidatorProtocol, ...]:
    validators: dict[str, ValidatorProtocol] = {}
    directory = private_validators_dir(path)
    if directory.is_dir():
        for validator_path in sorted(directory.glob("*.py")):
            if validator_path.name.startswith("_"):
                continue
            validator = _load_validator(validator_path)
            if validator.definition.name in validators:
                raise ConfigError(f"Duplicate private validator name: {validator.definition.name}")
            validators[validator.definition.name] = validator
    return tuple(validators[name] for name in sorted(validators))


def select_validator(
    validators: tuple[ValidatorProtocol, ...],
    name: str,
) -> ValidatorProtocol:
    for validator in validators:
        if validator.definition.name == name:
            return validator
    names = ", ".join(validator.definition.name for validator in validators)
    if names:
        raise ConfigError(f"Unknown validator {name!r}. Available: {names}")
    raise ConfigError("No validators found.")


def _load_validator(path: Path) -> ValidatorProtocol:
    module_name = f"centric_api_private_validator_{path.stem}_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Could not load validator file: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise ConfigError(f"Could not import validator file {path}: {exc}") from exc
    validator = _module_validator(module)
    _validate_validator(validator, path)
    return validator


def _module_validator(module: Any) -> Any:
    if hasattr(module, "get_validator"):
        return module.get_validator()
    if hasattr(module, "VALIDATOR"):
        return module.VALIDATOR
    raise ConfigError("Validator file must expose VALIDATOR or get_validator().")


def _validate_validator(validator: Any, path: Path) -> None:
    definition = getattr(validator, "definition", None)
    if not isinstance(definition, ValidationDefinition):
        raise ConfigError(f"Validator {path} must expose a ValidationDefinition as definition.")
    if not isinstance(definition.name, str) or not definition.name.strip():
        raise ConfigError(f"Validator {path} definition.name must be a non-empty string.")
    if not isinstance(definition.title, str) or not definition.title.strip():
        raise ConfigError(f"Validator {path} definition.title must be a non-empty string.")
    if isinstance(definition.required_endpoints, str) or not isinstance(
        definition.required_endpoints, tuple
    ):
        raise ConfigError(f"Validator {definition.name} required_endpoints must be a tuple.")
    if not callable(getattr(validator, "run", None)):
        raise ConfigError(f"Validator {definition.name} must implement run(ctx).")
