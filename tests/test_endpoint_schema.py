from __future__ import annotations

from pathlib import Path

import pytest

from centric_api.config import ConfigError
from centric_api.schema import load_endpoint_schemas


def test_schema_requires_endpoints_root(tmp_path: Path) -> None:
    schema = tmp_path / "endpoint-schema.yml"
    schema.write_text(
        """
styles:
  delete_when_any:
    - field: active
      equals: false
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="endpoints"):
        load_endpoint_schemas(schema)


def test_endpoint_schema_rejects_unknown_keys_and_versions(tmp_path: Path) -> None:
    root_schema = tmp_path / "root-schema.yml"
    root_schema.write_text(
        """
version: 1
unknown: nope
endpoints: {}
""",
        encoding="utf-8",
    )
    version_schema = tmp_path / "version-schema.yml"
    version_schema.write_text(
        """
version: 2
endpoints: {}
""",
        encoding="utf-8",
    )
    endpoint_schema = tmp_path / "endpoint-schema.yml"
    endpoint_schema.write_text(
        """
version: 1
endpoints:
  styles:
    typo: nope
""",
        encoding="utf-8",
    )
    condition_schema = tmp_path / "condition-schema.yml"
    condition_schema.write_text(
        """
version: 1
endpoints:
  styles:
    delete_when_any:
      - field: active
        equals: false
        typo: nope
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unknown keys: unknown"):
        load_endpoint_schemas(root_schema)
    with pytest.raises(ConfigError, match="version must be 1"):
        load_endpoint_schemas(version_schema)
    with pytest.raises(ConfigError, match="unknown keys: typo"):
        load_endpoint_schemas(endpoint_schema)
    with pytest.raises(ConfigError, match="unknown keys: typo"):
        load_endpoint_schemas(condition_schema)
