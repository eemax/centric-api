from __future__ import annotations

import json
from pathlib import Path

from centric_api.cli import main
from centric_api.store import connect


def test_snapshot_list_check_and_build_private_snapshot(tmp_path: Path, capsys) -> None:
    snapshots_dir = tmp_path / "snapshots-private"
    snapshots_dir.mkdir()
    _write_demo_snapshot(snapshots_dir / "dpp.py")
    db_path = tmp_path / "centric.db"
    _insert_style(
        db_path,
        "S2",
        {
            "id": "S2",
            "node_name": "Style 2",
            "concept": "Concept A",
            "season": "SS27",
            "brand": "Brand A",
        },
    )
    _insert_style(
        db_path,
        "S1",
        {
            "id": "S1",
            "node_name": "Style 1",
            "concept": "Concept A",
            "season": "SS27",
            "brand": "Brand A",
        },
    )

    assert main(["snapshot", "list", "--snapshots-dir", str(snapshots_dir), "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows == [
        {
            "name": "dpp",
            "title": "DPP Snapshot",
            "version": "test",
            "group_levels": ["concept", "season", "brand"],
            "required_endpoints": ["styles"],
            "description": "Demo private DPP snapshot.",
        }
    ]

    assert (
        main(
            [
                "snapshot",
                "check",
                "dpp",
                "--snapshots-dir",
                str(snapshots_dir),
                "--db",
                str(db_path),
                "--json",
            ]
        )
        == 0
    )
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["action"] == "check"
    assert check_payload["records"] == 2
    assert check_payload["output_dir"] is None

    output_root = tmp_path / "snapshot-output"
    assert (
        main(
            [
                "snapshot",
                "build",
                "dpp",
                "--snapshots-dir",
                str(snapshots_dir),
                "--db",
                str(db_path),
                "--output-dir",
                str(output_root),
                "--json",
            ]
        )
        == 0
    )
    build_payload = json.loads(capsys.readouterr().out)
    assert build_payload["action"] == "build"
    assert build_payload["records"] == 2
    assert build_payload["groups"] == 1
    assert build_payload["streams"] == 1
    snapshot_dir = output_root / "dpp" / "candidate"
    assert build_payload["output_dir"] == str(snapshot_dir)

    style_boms = snapshot_dir / "Concept A" / "SS27" / "Brand A" / "style-boms.jsonl"
    rows = [json.loads(line) for line in style_boms.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {"_key": "style-bom:S1", "node_name": "Style 1", "style_id": "S1"},
        {"_key": "style-bom:S2", "node_name": "Style 2", "style_id": "S2"},
    ]
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "generated_at" not in manifest
    assert manifest["files"][0]["path"] == "Concept A/SS27/Brand A/style-boms.jsonl"
    assert manifest["record_count"] == 2


def test_snapshot_build_clean_preserves_git_directory(tmp_path: Path, capsys) -> None:
    snapshots_dir = tmp_path / "snapshots-private"
    snapshots_dir.mkdir()
    _write_demo_snapshot(snapshots_dir / "dpp.py")
    db_path = tmp_path / "centric.db"
    _insert_style(
        db_path,
        "S1",
        {
            "id": "S1",
            "node_name": "Style 1",
            "concept": "Concept A",
            "season": "SS27",
            "brand": "Brand A",
        },
    )
    output_root = tmp_path / "snapshot-output"
    snapshot_dir = output_root / "dpp" / "candidate"
    (snapshot_dir / ".git").mkdir(parents=True)
    (snapshot_dir / "stale.txt").write_text("stale", encoding="utf-8")

    assert (
        main(
            [
                "snapshot",
                "build",
                "dpp",
                "--snapshots-dir",
                str(snapshots_dir),
                "--db",
                str(db_path),
                "--output-dir",
                str(output_root),
                "--clean",
            ]
        )
        == 0
    )

    assert (snapshot_dir / ".git").is_dir()
    assert not (snapshot_dir / "stale.txt").exists()
    assert (snapshot_dir / "manifest.json").is_file()
    assert "Snapshot build: dpp" in capsys.readouterr().out


def test_snapshot_build_refuses_unmanaged_non_empty_directory(tmp_path: Path, capsys) -> None:
    snapshots_dir = tmp_path / "snapshots-private"
    snapshots_dir.mkdir()
    _write_demo_snapshot(snapshots_dir / "dpp.py")
    db_path = tmp_path / "centric.db"
    _insert_style(
        db_path,
        "S1",
        {
            "id": "S1",
            "node_name": "Style 1",
            "concept": "Concept A",
            "season": "SS27",
            "brand": "Brand A",
        },
    )
    output_root = tmp_path / "snapshot-output"
    snapshot_dir = output_root / "dpp" / "candidate"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "stale.txt").write_text("stale", encoding="utf-8")

    assert (
        main(
            [
                "snapshot",
                "build",
                "dpp",
                "--snapshots-dir",
                str(snapshots_dir),
                "--db",
                str(db_path),
                "--output-dir",
                str(output_root),
            ]
        )
        == 1
    )

    assert "Use --clean" in capsys.readouterr().err


def test_snapshot_rejects_unsafe_snapshot_name(tmp_path: Path, capsys) -> None:
    snapshots_dir = tmp_path / "snapshots-private"
    snapshots_dir.mkdir()
    (snapshots_dir / "bad.py").write_text(
        """
from centric_api.snapshot import SnapshotDefinition, SnapshotOutput


class BadSnapshot:
    definition = SnapshotDefinition(name="../bad", title="Bad Snapshot")

    def build(self, ctx):
        return SnapshotOutput(records=())


SNAPSHOT = BadSnapshot()
""",
        encoding="utf-8",
    )

    assert main(["snapshot", "list", "--snapshots-dir", str(snapshots_dir)]) == 1
    assert "definition.name must contain only" in capsys.readouterr().err


def test_snapshot_group_parts_do_not_create_hidden_directories(tmp_path: Path) -> None:
    snapshots_dir = tmp_path / "snapshots-private"
    snapshots_dir.mkdir()
    _write_demo_snapshot(snapshots_dir / "dpp.py")
    db_path = tmp_path / "centric.db"
    _insert_style(
        db_path,
        "S1",
        {
            "id": "S1",
            "node_name": "Style 1",
            "concept": ".git",
            "season": "..",
            "brand": ".Brand A",
        },
    )
    output_root = tmp_path / "snapshot-output"

    assert (
        main(
            [
                "snapshot",
                "build",
                "dpp",
                "--snapshots-dir",
                str(snapshots_dir),
                "--db",
                str(db_path),
                "--output-dir",
                str(output_root),
            ]
        )
        == 0
    )

    snapshot_dir = output_root / "dpp" / "candidate"
    assert not (snapshot_dir / ".git").exists()
    assert (snapshot_dir / "git" / "Unknown" / "Brand A" / "style-boms.jsonl").is_file()


def test_snapshot_build_can_target_baseline_workspace(tmp_path: Path, capsys) -> None:
    snapshots_dir = tmp_path / "snapshots-private"
    snapshots_dir.mkdir()
    _write_demo_snapshot(snapshots_dir / "dpp.py")
    db_path = tmp_path / "centric.db"
    _insert_style(
        db_path,
        "S1",
        {
            "id": "S1",
            "node_name": "Style 1",
            "concept": "Concept A",
            "season": "SS27",
            "brand": "Brand A",
        },
    )
    output_root = tmp_path / "snapshot-output"

    assert (
        main(
            [
                "snapshot",
                "build",
                "dpp",
                "--snapshots-dir",
                str(snapshots_dir),
                "--db",
                str(db_path),
                "--output-dir",
                str(output_root),
                "--target",
                "baseline",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    baseline_dir = output_root / "dpp" / "baseline"
    candidate_dir = output_root / "dpp" / "candidate"
    assert payload["output_dir"] == str(baseline_dir)
    assert (baseline_dir / "manifest.json").is_file()
    assert not candidate_dir.exists()


def test_snapshot_promote_copies_candidate_to_baseline(tmp_path: Path, capsys) -> None:
    snapshots_dir = tmp_path / "snapshots-private"
    snapshots_dir.mkdir()
    _write_demo_snapshot(snapshots_dir / "dpp.py")
    db_path = tmp_path / "centric.db"
    _insert_style(
        db_path,
        "S1",
        {
            "id": "S1",
            "node_name": "Style 1",
            "concept": "Concept A",
            "season": "SS27",
            "brand": "Brand A",
        },
    )
    output_root = tmp_path / "snapshot-output"

    assert (
        main(
            [
                "snapshot",
                "build",
                "dpp",
                "--snapshots-dir",
                str(snapshots_dir),
                "--db",
                str(db_path),
                "--output-dir",
                str(output_root),
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "snapshot",
                "promote",
                "dpp",
                "--snapshots-dir",
                str(snapshots_dir),
                "--output-dir",
                str(output_root),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    candidate_dir = output_root / "dpp" / "candidate"
    baseline_dir = output_root / "dpp" / "baseline"
    assert payload["action"] == "promote"
    assert payload["output_dir"] == str(baseline_dir)
    assert (baseline_dir / "manifest.json").read_text(encoding="utf-8") == (
        candidate_dir / "manifest.json"
    ).read_text(encoding="utf-8")
    assert (baseline_dir / "Concept A" / "SS27" / "Brand A" / "style-boms.jsonl").read_text(
        encoding="utf-8"
    ) == (candidate_dir / "Concept A" / "SS27" / "Brand A" / "style-boms.jsonl").read_text(
        encoding="utf-8"
    )


def test_snapshot_promote_requires_candidate_manifest(tmp_path: Path, capsys) -> None:
    snapshots_dir = tmp_path / "snapshots-private"
    snapshots_dir.mkdir()
    _write_demo_snapshot(snapshots_dir / "dpp.py")

    assert (
        main(
            [
                "snapshot",
                "promote",
                "dpp",
                "--snapshots-dir",
                str(snapshots_dir),
                "--output-dir",
                str(tmp_path / "snapshot-output"),
            ]
        )
        == 1
    )

    assert "candidate manifest not found" in capsys.readouterr().err


def test_snapshot_duplicate_detection_normalizes_stream_filenames(
    tmp_path: Path,
    capsys,
) -> None:
    snapshots_dir = tmp_path / "snapshots-private"
    snapshots_dir.mkdir()
    (snapshots_dir / "dpp.py").write_text(
        """
from centric_api.snapshot import SnapshotDefinition, SnapshotOutput


class DppSnapshot:
    definition = SnapshotDefinition(name="dpp", title="DPP Snapshot")

    def build(self, ctx):
        return SnapshotOutput(
            records=(
                ctx.record("materials", "M1", {"material_id": "M1"}),
                ctx.record("materials.jsonl", "M1", {"material_id": "M1"}),
            )
        )


SNAPSHOT = DppSnapshot()
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "centric.db"
    with connect(db_path):
        pass

    assert (
        main(
            [
                "snapshot",
                "check",
                "dpp",
                "--snapshots-dir",
                str(snapshots_dir),
                "--db",
                str(db_path),
            ]
        )
        == 1
    )
    assert "duplicate record ./materials.jsonl:M1" in capsys.readouterr().err


def _insert_style(db_path: Path, record_id: str, payload: dict[str, object]) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO endpoint_records (
                endpoint, record_id, payload_json, payload_sha256, modified_at,
                source_file, source_run_id, ingested_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "styles",
                record_id,
                json.dumps(payload, sort_keys=True),
                record_id,
                None,
                "styles.jsonl",
                "run-1",
                "2026-01-01T00:00:00Z",
            ],
        )


def _write_demo_snapshot(path: Path) -> None:
    path.write_text(
        """
from centric_api.snapshot import SnapshotDefinition, SnapshotOutput


class DppSnapshot:
    definition = SnapshotDefinition(
        name="dpp",
        title="DPP Snapshot",
        required_endpoints=("styles",),
        description="Demo private DPP snapshot.",
        version="test",
    )

    def build(self, ctx):
        records = []
        for style in ctx.records("styles"):
            group = (
                style.get("concept") or "Unknown",
                style.get("season") or "Unknown",
                style.get("brand") or "Unknown",
            )
            style_id = str(style["id"])
            records.append(
                ctx.record(
                    "style-boms",
                    f"style-bom:{style_id}",
                    {"style_id": style_id, "node_name": style.get("node_name")},
                    group=group,
                )
            )
        return SnapshotOutput(records=tuple(records), metrics={"styles": len(records)})


SNAPSHOT = DppSnapshot()
""",
        encoding="utf-8",
    )
