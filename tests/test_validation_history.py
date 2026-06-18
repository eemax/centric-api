from __future__ import annotations

import json
from pathlib import Path

from centric_api.cli import main


def test_validate_history_groups_latest_run_and_writes_outputs(
    tmp_path: Path,
    capsys,
) -> None:
    runs_dir = tmp_path / "runs"
    output_dir = tmp_path / "history"
    _write_history_run(
        runs_dir,
        validator="style-readiness",
        run_id="run-old",
        started_at="2026-05-25T09:00:00Z",
        value=10.0,
        brand="CRAFT",
        trend="up",
    )
    _write_history_run(
        runs_dir,
        validator="style-readiness",
        run_id="run-new",
        started_at="2026-06-03T09:00:00Z",
        value=20.0,
        brand="CRAFT",
        trend="up",
        report_filename="report_26-06-03-0900.xlsx",
    )
    _write_history_run(
        runs_dir,
        validator="dpp-readiness",
        run_id="dpp-run",
        started_at="2026-06-03T09:00:00Z",
        value=30.0,
        brand="CRAFT",
    )

    assert (
        main(
            [
                "validate",
                "history",
                "--runs-dir",
                str(runs_dir),
                "--output-dir",
                str(output_dir),
                "--group",
                "week",
                "--validator",
                "style-readiness",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["run_count"] == 2
    assert payload["raw_metric_count"] == 2
    assert payload["point_count"] == 2
    assert payload["validators"] == ["style-readiness"]
    history_payload = json.loads((output_dir / "history.json").read_text(encoding="utf-8"))
    assert len(history_payload["points"]) == 2
    latest_point = history_payload["points"][-1]
    assert latest_point["run_id"] == "run-new"
    assert latest_point["value"] == 20.0
    assert latest_point["trend"] == "up"
    assert "workbook_path" not in payload
    assert not (output_dir / "history.xlsx").exists()
    html = (output_dir / "history.html").read_text(encoding="utf-8")
    assert "__HISTORY_JSON__" not in html
    assert "const historyPayload =" in html
    assert '"raw_points"' not in html
    assert '"report_path": "../runs/style-readiness/run-new/report_26-06-03-0900.xlsx"' in html
    assert 'select id="brandSelect" multiple' in html
    assert 'id="allBrands"' in html
    assert 'select id="conceptSelect" multiple' in html
    assert 'id="allConcepts"' in html
    assert 'select id="seasonType"' in html
    assert 'select id="seasonSelect" multiple' in html
    assert "brandFilter" not in html


def test_validate_history_counts_same_run_id_per_validator_and_skips_bad_schema(
    tmp_path: Path,
    capsys,
) -> None:
    runs_dir = tmp_path / "runs"
    output_dir = tmp_path / "history"
    _write_history_run(
        runs_dir,
        validator="style-readiness",
        run_id="shared-run-id",
        started_at="2026-06-03T09:00:00Z",
        value=20.0,
        brand="CRAFT",
    )
    _write_history_run(
        runs_dir,
        validator="dpp-readiness",
        run_id="shared-run-id",
        started_at="2026-06-03T10:00:00Z",
        value=30.0,
        brand="CRAFT",
    )
    bad_history_dir = runs_dir / "bad-validator" / "bad-run"
    bad_history_dir.mkdir(parents=True)
    (bad_history_dir / "history.json").write_text(
        json.dumps({"schema_version": 1, "metrics": []}),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "validate",
                "history",
                "--runs-dir",
                str(runs_dir),
                "--output-dir",
                str(output_dir),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["run_count"] == 2
    assert payload["raw_metric_count"] == 2
    history_payload = json.loads((output_dir / "history.json").read_text(encoding="utf-8"))
    assert history_payload["validators"] == ["dpp-readiness", "style-readiness"]


def test_validate_history_marks_downward_metric_improvement(
    tmp_path: Path,
    capsys,
) -> None:
    runs_dir = tmp_path / "runs"
    output_dir = tmp_path / "history"
    _write_history_run(
        runs_dir,
        validator="style-readiness",
        run_id="run-old",
        started_at="2026-06-01T09:00:00Z",
        metric="Styles With Blocking Issues",
        value=8,
        unit="count",
        brand="CRAFT",
        trend="down",
    )
    _write_history_run(
        runs_dir,
        validator="style-readiness",
        run_id="run-new",
        started_at="2026-06-08T09:00:00Z",
        metric="Styles With Blocking Issues",
        value=3,
        unit="count",
        brand="CRAFT",
        trend="down",
    )

    assert (
        main(
            [
                "validate",
                "history",
                "--runs-dir",
                str(runs_dir),
                "--output-dir",
                str(output_dir),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["point_count"] == 2
    assert not (output_dir / "history.xlsx").exists()
    history_payload = json.loads((output_dir / "history.json").read_text(encoding="utf-8"))
    assert history_payload["points"][-1]["trend"] == "down"


def test_validate_history_keeps_season_dimensions_as_separate_series(
    tmp_path: Path,
    capsys,
) -> None:
    runs_dir = tmp_path / "runs"
    output_dir = tmp_path / "history"
    _write_history_run(
        runs_dir,
        validator="style-readiness",
        run_id="run-one",
        started_at="2026-06-01T09:00:00Z",
        value=25,
        brand="CRAFT",
        trend="up",
        scope="concept_brand_season",
        dimensions={
            "concept": "Craft",
            "brand": "CRAFT",
            "season_type": "cycle",
            "season_year": "2026",
            "season_slot": "1C",
            "season_label": "1C26",
        },
    )
    _write_history_run(
        runs_dir,
        validator="style-readiness",
        run_id="run-two",
        started_at="2026-06-01T10:00:00Z",
        value=75,
        brand="CRAFT",
        trend="up",
        scope="concept_brand_season",
        dimensions={
            "concept": "Craft",
            "brand": "CRAFT",
            "season_type": "cycle",
            "season_year": "2026",
            "season_slot": "2C",
            "season_label": "2C26",
        },
    )

    assert (
        main(
            [
                "validate",
                "history",
                "--runs-dir",
                str(runs_dir),
                "--output-dir",
                str(output_dir),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["point_count"] == 2
    history_payload = json.loads((output_dir / "history.json").read_text(encoding="utf-8"))
    assert {point["dimensions"]["season_label"] for point in history_payload["points"]} == {
        "1C26",
        "2C26",
    }
    assert {point["dimensions"]["concept"] for point in history_payload["points"]} == {
        "Craft"
    }
    assert not (output_dir / "history.xlsx").exists()


def _write_history_run(
    runs_dir: Path,
    *,
    validator: str,
    run_id: str,
    started_at: str,
    value: float,
    brand: str,
    metric: str = "Style Completion %",
    unit: str = "percent",
    trend: str = "neutral",
    scope: str = "brand",
    dimensions: dict[str, str] | None = None,
    report_filename: str | None = None,
) -> None:
    run_dir = runs_dir / validator / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "validator": validator,
        "title": validator.replace("-", " ").title(),
        "run_id": run_id,
        "status": "failed",
        "started_at": started_at,
        "finished_at": started_at,
        "metrics": [
            {
                "scope": scope,
                "brand": brand,
                "metric": metric,
                "value": value,
                "unit": unit,
                "trend": trend,
                "numerator": value,
                "denominator": 100,
                "dimensions": dimensions or {},
            }
        ],
    }
    if report_filename is not None:
        payload["report_path"] = str(run_dir / report_filename)
    (run_dir / "history.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
