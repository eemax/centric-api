from __future__ import annotations

import json
from pathlib import Path

from openpyxl import load_workbook

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
        started_at="2026-06-01T09:00:00Z",
        value=10.0,
        brand="CRAFT",
    )
    _write_history_run(
        runs_dir,
        validator="style-readiness",
        run_id="run-new",
        started_at="2026-06-03T09:00:00Z",
        value=20.0,
        brand="CRAFT",
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
    assert payload["point_count"] == 1
    assert payload["validators"] == ["style-readiness"]
    history_payload = json.loads((output_dir / "history.json").read_text(encoding="utf-8"))
    assert len(history_payload["points"]) == 1
    assert history_payload["points"][0]["run_id"] == "run-new"
    assert history_payload["points"][0]["value"] == 20.0
    workbook = load_workbook(output_dir / "history.xlsx", read_only=True)
    assert workbook.sheetnames == ["History", "Latest", "Runs"]
    html = (output_dir / "history.html").read_text(encoding="utf-8")
    assert "__HISTORY_JSON__" not in html
    assert "const historyPayload =" in html


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
        json.dumps({"schema_version": "not-a-number", "metrics": []}),
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


def _write_history_run(
    runs_dir: Path,
    *,
    validator: str,
    run_id: str,
    started_at: str,
    value: float,
    brand: str,
) -> None:
    run_dir = runs_dir / validator / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "history.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "validator": validator,
                "title": validator.replace("-", " ").title(),
                "run_id": run_id,
                "status": "failed",
                "started_at": started_at,
                "finished_at": started_at,
                "metrics": [
                    {
                        "scope": "brand",
                        "brand": brand,
                        "metric": "Style Completion %",
                        "value": value,
                        "unit": "percent",
                        "numerator": value,
                        "denominator": 100,
                        "dimensions": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
