from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path

from centric_api.cli import main
from centric_api.raw_evidence import (
    RawCompactResult,
    RawIndexRunResult,
    RawObservation,
    raw_index_manifest_fields,
    raw_index_path,
)
from centric_api.store import discover_raw_files


def test_raw_evidence_result_types_keep_public_facade_identity() -> None:
    values = (
        RawIndexRunResult(
            run_path=Path("raw/runs/run-1"),
            run_id="run-1",
            status="ok",
            indexed_files=1,
            skipped_files=0,
            errors=(),
        ),
        RawObservation(
            endpoint="styles",
            record_id="S1",
            run_id="run-1",
            run_started_at="2026-01-01T00:00:00Z",
            raw_file=Path("styles.jsonl"),
            index_file=Path("styles.index.jsonl"),
            line=1,
            payload_sha256="payload",
            raw_line_sha256="line",
            modified_at="2026-01-01T00:00:00Z",
            delete_type=None,
            manifest_path=Path("manifest.json"),
        ),
        RawCompactResult(
            status="ok",
            output_dir=Path("raw/runs/compact"),
            source_run_count=1,
            source_record_count=1,
            winner_count=1,
            written_count=1,
            deleted_winner_count=0,
            archived_count=0,
            dry_run=False,
            counts_exact=True,
        ),
    )

    for value in values:
        assert type(value).__module__ == "centric_api.raw_evidence"
        assert pickle.loads(pickle.dumps(value)) == value


def test_raw_check_inspect_and_diff_use_record_indexes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    _write_indexed_run(
        tmp_path,
        "run-1",
        [{"id": "S1", "node_name": "Old", "_modified_at": "2026-01-01T00:00:00Z"}],
    )
    _write_indexed_run(
        tmp_path,
        "run-2",
        [{"id": "S1", "node_name": "New", "_modified_at": "2026-01-02T00:00:00Z"}],
    )

    assert main(["raw", "check", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["status"] == "ok"
    assert check_payload["runs"][0]["files"][0]["record_count"] == 1

    assert main(["raw", "inspect", "styles", "S1", "--json"]) == 0
    inspect_payload = json.loads(capsys.readouterr().out)
    assert inspect_payload["matches"] == 2
    assert [item["run_id"] for item in inspect_payload["observations"]] == ["run-1", "run-2"]

    assert main(["raw", "diff", "styles", "S1", "--json"]) == 0
    diff_payload = json.loads(capsys.readouterr().out)
    assert diff_payload["from"]["run_id"] == "run-1"
    assert diff_payload["to"]["run_id"] == "run-2"
    assert {
        (change["path"], change["from"], change["to"]) for change in diff_payload["changes"]
    } >= {
        ("node_name", "Old", "New"),
        ("_modified_at", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"),
    }


def test_raw_compact_writes_full_run_and_can_archive_sources(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    _write_indexed_run(
        tmp_path,
        "run-1",
        [
            {"id": "S1", "node_name": "Old", "_modified_at": "2026-01-01T00:00:00Z"},
            {"id": "S2", "node_name": "Keep", "_modified_at": "2026-01-01T00:00:00Z"},
        ],
    )
    _write_indexed_run(
        tmp_path,
        "run-2",
        [{"id": "S1", "node_name": "New", "_modified_at": "2026-01-02T00:00:00Z"}],
    )
    output = tmp_path / "raw" / "runs" / "compact-1"

    assert (
        main(
            [
                "raw",
                "compact",
                "--output",
                str(output),
                "--archive-old",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["source_run_count"] == 2
    assert payload["winner_count"] == 2
    assert payload["written_count"] == 2
    assert payload["archived_count"] == 2
    assert payload["counts_exact"] is True
    assert (output / "styles.jsonl").is_file()
    assert (output / "styles.index.jsonl").is_file()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "compacted-full"
    assert manifest["endpoints"]["styles"]["record_count"] == 2
    compacted_rows = [
        json.loads(line)
        for line in (output / "styles.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {row["id"]: row["node_name"] for row in compacted_rows} == {
        "S1": "New",
        "S2": "Keep",
    }
    assert not (tmp_path / "raw" / "runs" / "run-1").exists()
    assert not (tmp_path / "raw" / "runs" / "run-2").exists()
    assert (tmp_path / "raw" / "archive" / "run-1").is_dir()
    assert (tmp_path / "raw" / "archive" / "run-2").is_dir()


def test_raw_compact_respects_full_snapshot_omissions(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    _write_indexed_run(
        tmp_path,
        "run-1",
        [
            {"id": "S1", "node_name": "Keep", "_modified_at": "2026-01-01T00:00:00Z"},
            {"id": "S2", "node_name": "Deleted", "_modified_at": "2026-01-01T00:00:00Z"},
        ],
        mode="full",
    )
    _write_indexed_run(
        tmp_path,
        "run-2",
        [{"id": "S1", "node_name": "Keep", "_modified_at": "2026-01-02T00:00:00Z"}],
        mode="full",
    )
    output = tmp_path / "raw" / "runs" / "compact-1"

    assert main(["raw", "compact", "--output", str(output), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    rows = [
        json.loads(line)
        for line in (output / "styles.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert payload["winner_count"] == 1
    assert payload["written_count"] == 1
    assert [row["id"] for row in rows] == ["S1"]


def test_raw_discovery_ignores_index_sidecars(tmp_path: Path) -> None:
    run_dir = _write_indexed_run(
        tmp_path,
        "run-1",
        [{"id": "S1", "node_name": "Style", "_modified_at": "2026-01-01T00:00:00Z"}],
    )

    files = discover_raw_files(tmp_path / "raw")

    assert [file.path for file in files] == [run_dir / "styles.delta.jsonl"]


def test_raw_check_detects_index_payload_mismatch(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    run_dir = _write_indexed_run(
        tmp_path,
        "run-1",
        [{"id": "S1", "node_name": "Old", "_modified_at": "2026-01-01T00:00:00Z"}],
    )
    raw_path = run_dir / "styles.delta.jsonl"
    next_raw = (
        json.dumps(
            {"id": "S1", "node_name": "New", "_modified_at": "2026-01-01T00:00:00Z"},
            sort_keys=True,
        )
        + "\n"
    )
    raw_path.write_text(next_raw, encoding="utf-8")
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["endpoints"]["styles"]["content_sha256"] = hashlib.sha256(
        next_raw.encode("utf-8")
    ).hexdigest()
    manifest["endpoints"]["styles"]["byte_size"] = len(next_raw.encode("utf-8"))
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    assert main(["raw", "check", "run-1", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert "raw line hash mismatch at line 1" in payload["runs"][0]["files"][0]["errors"]
    assert "raw payload hash mismatch at line 1" in payload["runs"][0]["files"][0]["errors"]


def test_raw_check_run_id_respects_raw_dir_override(
    tmp_path: Path,
    capsys,
) -> None:
    raw_root = tmp_path / "custom-raw"
    _write_indexed_run_at_raw_root(
        raw_root,
        "run-1",
        [{"id": "S1", "node_name": "Style", "_modified_at": "2026-01-01T00:00:00Z"}],
    )

    assert main(["raw", "check", "run-1", "--raw-dir", str(raw_root), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["runs"][0]["run_id"] == "run-1"
    assert (raw_root / "runs" / "run-1" / ".verified.json").is_file()


def test_raw_check_accepts_successful_zero_row_endpoint_without_file(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    run_dir = _write_indexed_run(
        tmp_path,
        "run-1",
        [{"id": "S1", "node_name": "Style", "_modified_at": "2026-01-01T00:00:00Z"}],
    )
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["endpoints"]["empty_endpoint"] = {
        "endpoint": "empty_endpoint",
        "status": "OK",
        "expected_count": 0,
        "file": None,
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    assert main(["raw", "check", "run-1", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    empty_file = next(
        item for item in payload["runs"][0]["files"] if item["endpoint"] == "empty_endpoint"
    )
    assert empty_file["status"] == "ok"
    assert empty_file["record_count"] == 0


def test_raw_compact_archive_collision_fails_before_writing_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    _write_indexed_run(
        tmp_path,
        "run-1",
        [{"id": "S1", "node_name": "Style", "_modified_at": "2026-01-01T00:00:00Z"}],
    )
    output = tmp_path / "raw" / "runs" / "compact-1"
    archive_collision = tmp_path / "raw" / "archive" / "run-1"
    archive_collision.mkdir(parents=True)

    assert (
        main(
            [
                "raw",
                "compact",
                "--output",
                str(output),
                "--archive-old",
                "--json",
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert "Raw archive target already exists" in captured.err
    assert not output.exists()
    assert (tmp_path / "raw" / "runs" / "run-1").is_dir()


def test_raw_compact_refuses_unindexed_legacy_runs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    _write_indexed_run(
        tmp_path,
        "run-1",
        [{"id": "S1", "node_name": "Style", "_modified_at": "2026-01-01T00:00:00Z"}],
    )
    _write_legacy_run_without_index(tmp_path, "legacy-run")

    assert main(["raw", "compact", "--dry-run", "--json"]) == 1

    captured = capsys.readouterr()
    assert "Raw compaction requires verified indexed runs" in captured.err
    assert "legacy-run=warn" in captured.err


def test_raw_index_repairs_missing_sidecar_before_compaction(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    _write_legacy_run_without_index(tmp_path, "legacy-run")

    assert main(["raw", "index", "legacy-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["indexed_files"] == 1
    assert payload["skipped_files"] == 0

    run_dir = tmp_path / "raw" / "runs" / "legacy-run"
    raw_path = run_dir / "styles.delta.jsonl"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["endpoints"]["styles"]["index_file"] == raw_index_path(raw_path).name
    assert raw_index_path(raw_path).is_file()

    assert main(["raw", "check", "legacy-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"

    assert main(["raw", "compact", "--dry-run", "--json"]) == 0
    compact_payload = json.loads(capsys.readouterr().out)
    assert compact_payload["source_run_count"] == 1
    assert compact_payload["winner_count"] == 1


def test_raw_index_preflights_missing_files_before_writing_sidecars(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    run_dir = _write_legacy_run_without_index(tmp_path, "legacy-run")
    raw_path = run_dir / "styles.delta.jsonl"
    raw_path.unlink()

    assert main(["raw", "index", "legacy-run", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["indexed_files"] == 0
    assert "raw file missing" in payload["runs"][0]["errors"][0]
    assert not raw_index_path(raw_path).exists()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "index_file" not in manifest["endpoints"]["styles"]


def test_raw_index_refuses_invalid_raw_records_before_writing_sidecars(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    run_dir = _write_legacy_run_without_index(tmp_path, "legacy-run")
    raw_path = run_dir / "styles.delta.jsonl"
    raw_path.write_text('{"id": "S1"}\nnot-json\n', encoding="utf-8")

    assert main(["raw", "index", "legacy-run", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["indexed_files"] == 0
    assert "raw file has invalid records" in payload["runs"][0]["errors"][0]
    assert not raw_index_path(raw_path).exists()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "index_file" not in manifest["endpoints"]["styles"]


def test_raw_compact_rechecks_when_verification_seal_is_stale(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    run_dir = _write_indexed_run(
        tmp_path,
        "run-1",
        [{"id": "S1", "node_name": "Style", "_modified_at": "2026-01-01T00:00:00Z"}],
    )

    assert main(["raw", "check", "run-1", "--json"]) == 0
    assert (run_dir / ".verified.json").is_file()

    (run_dir / "styles.delta.jsonl").write_text(
        json.dumps(
            {"id": "S1", "node_name": "Changed", "_modified_at": "2026-01-01T00:00:00Z"},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["raw", "compact", "--dry-run", "--json"]) == 1

    captured = capsys.readouterr()
    assert "Raw compaction requires verified indexed runs" in captured.err
    assert "run-1=failed" in captured.err
    assert not (run_dir / ".verified.json").exists()


def test_raw_compact_includes_markerless_trusted_runs_and_skips_partial_runs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("CENTRIC_API_HOME", str(tmp_path))
    trusted = _write_indexed_run(
        tmp_path,
        "markerless-ok",
        [{"id": "S1", "node_name": "Trusted", "_modified_at": "2026-01-01T00:00:00Z"}],
    )
    (trusted / ".completed.json").unlink()
    partial = _write_indexed_run(
        tmp_path,
        "partial-run",
        [{"id": "S2", "node_name": "Partial", "_modified_at": "2026-01-01T00:00:00Z"}],
    )
    (partial / ".completed.json").unlink()
    partial_manifest_path = partial / "manifest.json"
    partial_manifest = json.loads(partial_manifest_path.read_text(encoding="utf-8"))
    partial_manifest["status"] = "PARTIAL"
    partial_manifest_path.write_text(json.dumps(partial_manifest, sort_keys=True), encoding="utf-8")

    assert main(["raw", "compact", "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["source_run_count"] == 1
    assert payload["winner_count"] == 1
    assert payload["written_count"] is None
    assert payload["deleted_winner_count"] is None
    assert payload["counts_exact"] is False

    assert main(["raw", "compact", "--dry-run", "--exact", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["source_run_count"] == 1
    assert payload["winner_count"] == 1
    assert payload["written_count"] == 1
    assert payload["deleted_winner_count"] == 0
    assert payload["counts_exact"] is True


def _write_indexed_run(
    tmp_path: Path,
    run_id: str,
    rows: list[dict[str, str]],
    *,
    mode: str = "delta",
) -> Path:
    return _write_indexed_run_at_raw_root(tmp_path / "raw", run_id, rows, mode=mode)


def _write_indexed_run_at_raw_root(
    raw_root: Path,
    run_id: str,
    rows: list[dict[str, str]],
    *,
    mode: str = "delta",
) -> Path:
    run_dir = raw_root / "runs" / run_id
    run_dir.mkdir(parents=True)
    raw_path = run_dir / "styles.delta.jsonl"
    raw_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    index_fields = raw_index_manifest_fields(raw_path, endpoint="styles")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "run_id": run_id,
                "mode": mode,
                "started_at": rows[-1]["_modified_at"],
                "finished_at": rows[-1]["_modified_at"],
                "endpoints": {
                    "styles": {
                        "endpoint": "styles",
                        "file": "styles.delta.jsonl",
                        "is_delta": mode != "full",
                        "items_fetched": len(rows),
                        **index_fields,
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (run_dir / ".completed.json").write_text(
        json.dumps({"status": "completed", "run_id": run_id}),
        encoding="utf-8",
    )
    return run_dir


def _write_legacy_run_without_index(tmp_path: Path, run_id: str) -> Path:
    run_dir = tmp_path / "raw" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "styles.delta.jsonl").write_text(
        json.dumps(
            {"id": "S1", "node_name": "Legacy", "_modified_at": "2026-01-01T00:00:00Z"},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "mode": "delta",
                "started_at": "2026-01-01T00:00:00Z",
                "endpoints": {
                    "styles": {
                        "endpoint": "styles",
                        "file": "styles.delta.jsonl",
                        "is_delta": True,
                        "items_fetched": 1,
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (run_dir / ".completed.json").write_text(
        json.dumps({"status": "completed", "run_id": run_id}),
        encoding="utf-8",
    )
    return run_dir
