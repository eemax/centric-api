from __future__ import annotations

from datetime import UTC, datetime

from centric_api.artifact_names import (
    allocate_artifact_dir,
    allocate_artifact_name,
    allocate_artifact_path,
    artifact_base_name,
    artifact_slug,
)


def test_artifact_base_name_is_name_then_minute_timestamp() -> None:
    assert artifact_slug("Style Readiness!") == "style-readiness"
    assert (
        artifact_base_name("Style Readiness", datetime(2026, 6, 19, 12, 34, 56, tzinfo=UTC))
        == "style-readiness-2026-06-19-1234"
    )


def test_allocate_artifact_name_suffixes_collisions() -> None:
    existing = {"style-readiness-2026-06-19-1234"}

    assert (
        allocate_artifact_name("style-readiness-2026-06-19-1234", existing.__contains__)
        == "style-readiness-2026-06-19-1234-2"
    )


def test_allocate_artifact_dir_creates_first_free_directory(tmp_path) -> None:
    (tmp_path / "style-readiness-2026-06-19-1234").mkdir()

    run_id, output_dir = allocate_artifact_dir(
        tmp_path,
        "Style Readiness",
        "2026-06-19T12:34:56Z",
    )

    assert run_id == "style-readiness-2026-06-19-1234-2"
    assert output_dir.is_dir()


def test_allocate_artifact_path_suffixes_existing_file(tmp_path) -> None:
    output_dir = tmp_path / "exports"
    output_dir.mkdir()
    (output_dir / "styles-export-2026-06-19-1234.xlsx").write_text("existing", encoding="utf-8")

    output_path = allocate_artifact_path(
        output_dir,
        "Styles Export",
        "2026-06-19T12:34:56Z",
        extension="xlsx",
    )

    assert output_path == output_dir / "styles-export-2026-06-19-1234-2.xlsx"


def test_allocate_artifact_path_creates_output_dir(tmp_path) -> None:
    output_dir = tmp_path / "exports"

    output_path = allocate_artifact_path(
        output_dir,
        "Styles Export",
        "2026-06-19T12:34:56Z",
        extension="xlsx",
    )

    assert output_path == output_dir / "styles-export-2026-06-19-1234.xlsx"
    assert output_dir.is_dir()
