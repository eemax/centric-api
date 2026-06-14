from __future__ import annotations

from pathlib import Path

import pytest

from centric_api.bundle_config import load_bundle_config
from centric_api.config import (
    load_fetcher_settings,
    resolve_optional_private_config_path,
    resolve_private_config_path,
    runtime_home,
    runtime_path,
)
from centric_api.download_config import load_download_config
from centric_api.load_config import load_load_config
from centric_api.schema import load_endpoint_schemas
from centric_api.units import load_unit_registry
from centric_api.view_config import load_view_config


def test_runtime_paths_use_centric_api_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path / "home"))

    assert runtime_home() == tmp_path / "home"
    assert runtime_path("raw") == tmp_path / "home" / "raw"


def test_explicit_private_config_paths_expand_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "user-home"
    monkeypatch.setenv("HOME", str(home))

    assert resolve_private_config_path("delta/state.json", "~/custom-state.json") == (
        home / "custom-state.json"
    )
    assert resolve_optional_private_config_path("load.yml", "~/custom-load.yml") == (
        home / "custom-load.yml"
    )


def test_default_configs_load_outside_repo_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path / "home"))

    fetcher_cfg, auth_settings, endpoints = load_fetcher_settings(Path("config/fetcher.yml"))
    download_config = load_download_config()
    bundle_config = load_bundle_config()
    view_config = load_view_config()
    load_config = load_load_config()
    units = load_unit_registry()
    endpoint_schemas = load_endpoint_schemas()

    assert fetcher_cfg.output_dir == tmp_path / "home" / "raw"
    assert auth_settings.env_file == tmp_path / "home" / "local.env"
    assert endpoints
    assert "product_sources" in {endpoint.name for endpoint in endpoints}
    bom_subtypes = next(endpoint for endpoint in endpoints if endpoint.name == "bom_subtypes")
    assert bom_subtypes.path == "apparel_bom_subtypes"
    assert bom_subtypes.count_spec.path == "count/ApparelBOMSubtype"
    assert download_config.jobs
    assert bundle_config.bundles
    assert view_config.views
    assert load_config.jobs
    assert units.dimensions
    assert "styles" in endpoint_schemas
    assert "product_sources" in endpoint_schemas
    assert "bom_subtypes" in endpoint_schemas
    bom_sections_schema = endpoint_schemas["bom_sections"]
    assert ("active", False) in {
        (condition.field, condition.equals) for condition in bom_sections_schema.delete_when_any
    }
    assert ("ad_hoc", True) in {
        (condition.field, condition.equals) for condition in bom_sections_schema.delete_when_any
    }
