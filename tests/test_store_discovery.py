from __future__ import annotations

import json
import pickle
from pathlib import Path

from centric_api.store import RawFile, discover_raw_files


def test_raw_file_facade_keeps_public_module_identity() -> None:
    assert RawFile.__module__ == "centric_api.store"


def test_raw_file_pickle_round_trip_uses_public_facade(tmp_path: Path) -> None:
    raw_file = RawFile(
        path=tmp_path / "styles.jsonl",
        endpoint="styles",
        is_delta=False,
        source_run_id="run-1",
    )

    restored = pickle.loads(pickle.dumps(raw_file))

    assert type(restored) is RawFile
    assert restored == raw_file


def test_discover_raw_files_applies_manifest_scope_and_sorting(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    root_file = raw_dir / "styles.delta.jsonl"
    root_file.parent.mkdir()
    root_file.write_text("{}\n", encoding="utf-8")

    early_run = raw_dir / "runs" / "early"
    early_run.mkdir(parents=True)
    (early_run / "colors.jsonl").write_text("{}\n", encoding="utf-8")
    (early_run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-early",
                "mode": "delta",
                "started_at": "2026-01-01T00:00:00Z",
                "endpoints": {
                    "colors": {
                        "file": "colors.jsonl",
                        "is_delta": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    late_run = raw_dir / "runs" / "late"
    late_run.mkdir(parents=True)
    (late_run / "styles.jsonl").write_text("{}\n", encoding="utf-8")
    (late_run / "unlisted.jsonl").write_text("{}\n", encoding="utf-8")
    (late_run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-late",
                "mode": "full",
                "started_at": "2026-01-02T00:00:00Z",
                "endpoints": {
                    "catalog_styles": {
                        "file": "styles.jsonl",
                        "is_delta": False,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    files = discover_raw_files(raw_dir)

    assert [file.endpoint for file in files] == ["colors", "catalog_styles", "styles"]
    assert [file.source_run_id for file in files] == ["run-early", "run-late", "root"]
    assert [file.run_mode for file in files] == ["delta", "full", None]
    assert [file.is_delta for file in files] == [True, False, True]
    assert [file.path.name for file in files] == [
        "colors.jsonl",
        "styles.jsonl",
        "styles.delta.jsonl",
    ]
    assert files[0].manifest_path == early_run / "manifest.json"
    assert files[1].manifest_path == late_run / "manifest.json"
    assert files[2].manifest_path is None
    assert all(type(file) is RawFile for file in files)
