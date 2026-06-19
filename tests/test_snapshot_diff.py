from __future__ import annotations

import json
from pathlib import Path

import pytest

from centric_api.config import ConfigError
from centric_api.snapshot import (
    SnapshotChange,
    SnapshotDefinition,
    SnapshotOutput,
    SnapshotRecord,
    SnapshotRecordIdentity,
)
from centric_api.snapshot.artifacts import write_snapshot_artifacts
from centric_api.snapshot.diffing import diff_snapshot_artifacts, promote_snapshot_review


def test_snapshot_diff_marks_style_boms_record_level_and_locks_material_impacts(
    tmp_path: Path,
) -> None:
    definition = SnapshotDefinition(name="dpp", title="DPP Snapshot")
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    _write_snapshot_artifact_set(
        baseline_dir,
        definition,
        style_weight=1,
        material_description="Plain weave",
        material_uom="m",
    )
    _write_snapshot_artifact_set(
        candidate_dir,
        definition,
        style_weight=2,
        material_description="Twill weave",
        material_uom="yd",
    )

    summary = diff_snapshot_artifacts(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        policy=_TestDppPolicy(),
        review_file=tmp_path / "review.json",
    )

    record_changes = [
        change for change in summary.changes if change.identity.stream == "style-boms"
    ]
    material_changes = [
        change for change in summary.changes if change.identity.stream == "materials"
    ]
    assert len(record_changes) == 1
    assert record_changes[0].promotion_unit == "record"
    assert record_changes[0].path is None
    assert {change.path: change.approval for change in material_changes} == {
        "/description": "actionable",
        "/uom": "locked",
    }
    locked = next(change for change in material_changes if change.path == "/uom")
    assert locked.approval_owner == "style-boms"
    assert locked.impacts == (
        SnapshotRecordIdentity(
            group=("Concept A", "SS27", "Brand A"),
            stream="style-boms",
            key="STYLE1|BOM1|style|",
        ),
    )

    review = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))
    assert review["metrics"]["locked"] == 1
    assert any(action["approval"] == "locked" for action in review["actions"])


def test_snapshot_review_promote_applies_material_only_fields_and_rejects_locked(
    tmp_path: Path,
) -> None:
    definition = SnapshotDefinition(name="dpp", title="DPP Snapshot")
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    _write_snapshot_artifact_set(
        baseline_dir,
        definition,
        style_weight=1,
        material_description="Plain weave",
        material_uom="m",
    )
    _write_snapshot_artifact_set(
        candidate_dir,
        definition,
        style_weight=2,
        material_description="Twill weave",
        material_uom="yd",
    )
    review_file = tmp_path / "review.json"
    summary = diff_snapshot_artifacts(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        policy=_TestDppPolicy(),
        review_file=review_file,
    )
    review = json.loads(review_file.read_text(encoding="utf-8"))
    assert review["schema_version"] == 1
    for action in review["actions"]:
        if action["stream"] == "materials" and action["path"] == "/description":
            action["action"] = "promote"
    review_file.write_text(json.dumps(review), encoding="utf-8")

    _manifest_path, _manifest, metrics = promote_snapshot_review(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        review_file=review_file,
        policy=_TestDppPolicy(),
    )

    assert metrics == {
        "promoted": 1,
        "skipped": len(summary.changes) - 1,
        "auto_promoted_locked": 0,
    }
    material = _first_jsonl_record(
        baseline_dir / "Concept A" / "SS27" / "Brand A" / "materials.jsonl"
    )
    style_bom = _first_jsonl_record(
        baseline_dir / "Concept A" / "SS27" / "Brand A" / "style-boms.jsonl"
    )
    assert material["description"] == "Twill weave"
    assert material["uom"] == "m"
    assert style_bom["total_weight_kg"] == 1

    diff_snapshot_artifacts(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        policy=_TestDppPolicy(),
        review_file=review_file,
    )
    locked_review = json.loads(review_file.read_text(encoding="utf-8"))
    for action in locked_review["actions"]:
        if action["stream"] == "materials" and action["path"] == "/uom":
            action["action"] = "promote"
    review_file.write_text(json.dumps(locked_review), encoding="utf-8")
    with pytest.raises(ConfigError, match="locked"):
        promote_snapshot_review(
            definition=definition,
            baseline_dir=baseline_dir,
            candidate_dir=candidate_dir,
            review_file=review_file,
            policy=_TestDppPolicy(),
        )


def test_snapshot_review_promote_rejects_wrong_schema_version(tmp_path: Path) -> None:
    definition = SnapshotDefinition(name="dpp", title="DPP Snapshot")
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    _write_snapshot_artifact_set(
        baseline_dir,
        definition,
        style_weight=1,
        material_description="Plain weave",
        material_uom="m",
    )
    _write_snapshot_artifact_set(
        candidate_dir,
        definition,
        style_weight=2,
        material_description="Twill weave",
        material_uom="yd",
    )
    review_file = tmp_path / "review.json"
    diff_snapshot_artifacts(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        policy=_TestDppPolicy(),
        review_file=review_file,
    )
    review = json.loads(review_file.read_text(encoding="utf-8"))
    review["schema_version"] = 0
    review_file.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(ConfigError, match="schema_version"):
        promote_snapshot_review(
            definition=definition,
            baseline_dir=baseline_dir,
            candidate_dir=candidate_dir,
            review_file=review_file,
            policy=_TestDppPolicy(),
        )


def test_snapshot_review_promote_rejects_unknown_action(tmp_path: Path) -> None:
    definition = SnapshotDefinition(name="dpp", title="DPP Snapshot")
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    _write_snapshot_artifact_set(
        baseline_dir,
        definition,
        style_weight=1,
        material_description="Plain weave",
        material_uom="m",
    )
    _write_snapshot_artifact_set(
        candidate_dir,
        definition,
        style_weight=2,
        material_description="Twill weave",
        material_uom="yd",
    )
    review_file = tmp_path / "review.json"
    diff_snapshot_artifacts(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        policy=_TestDppPolicy(),
        review_file=review_file,
    )
    review = json.loads(review_file.read_text(encoding="utf-8"))
    review["actions"][0]["action"] = "maybe"
    review_file.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(ConfigError, match="action must be one of"):
        promote_snapshot_review(
            definition=definition,
            baseline_dir=baseline_dir,
            candidate_dir=candidate_dir,
            review_file=review_file,
            policy=_TestDppPolicy(),
        )


def test_snapshot_review_promote_auto_promotes_locked_material_fields_from_bom_owner(
    tmp_path: Path,
) -> None:
    definition = SnapshotDefinition(name="dpp", title="DPP Snapshot")
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    _write_snapshot_artifact_set(
        baseline_dir,
        definition,
        style_weight=1,
        material_description="Plain weave",
        material_uom="m",
    )
    _write_snapshot_artifact_set(
        candidate_dir,
        definition,
        style_weight=2,
        material_description="Plain weave",
        material_uom="yd",
    )
    review_file = tmp_path / "review.json"
    diff_snapshot_artifacts(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        policy=_TestDppPolicy(),
        review_file=review_file,
    )
    review = json.loads(review_file.read_text(encoding="utf-8"))
    for action in review["actions"]:
        if action["stream"] == "style-boms":
            action["action"] = "promote"
    review_file.write_text(json.dumps(review), encoding="utf-8")

    _manifest_path, _manifest, metrics = promote_snapshot_review(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        review_file=review_file,
        policy=_TestDppPolicy(),
    )

    assert metrics == {"promoted": 1, "skipped": 1, "auto_promoted_locked": 1}
    material = _first_jsonl_record(
        baseline_dir / "Concept A" / "SS27" / "Brand A" / "materials.jsonl"
    )
    style_bom = _first_jsonl_record(
        baseline_dir / "Concept A" / "SS27" / "Brand A" / "style-boms.jsonl"
    )
    assert material["uom"] == "yd"
    assert style_bom["total_weight_kg"] == 2


def test_snapshot_review_promote_can_approve_locked_field_with_owner_only_action(
    tmp_path: Path,
) -> None:
    definition = SnapshotDefinition(name="dpp", title="DPP Snapshot")
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    _write_snapshot_artifact_set(
        baseline_dir,
        definition,
        style_weight=1,
        material_description="Plain weave",
        material_uom="m",
    )
    _write_snapshot_artifact_set(
        candidate_dir,
        definition,
        style_weight=1,
        material_description="Plain weave",
        material_uom="yd",
    )
    review_file = tmp_path / "review.json"
    summary = diff_snapshot_artifacts(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        policy=_TestDppPolicy(),
        review_file=review_file,
    )

    assert [
        (change.change_type, change.identity.stream, change.path) for change in summary.changes
    ] == [
        ("field_changed", "materials", "/uom"),
        ("owner_approval", "style-boms", None),
    ]
    review = json.loads(review_file.read_text(encoding="utf-8"))
    for action in review["actions"]:
        if action["change_type"] == "owner_approval":
            action["action"] = "promote"
    review_file.write_text(json.dumps(review), encoding="utf-8")

    _manifest_path, _manifest, metrics = promote_snapshot_review(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        review_file=review_file,
        policy=_TestDppPolicy(),
    )

    assert metrics == {"promoted": 1, "skipped": 1, "auto_promoted_locked": 1}
    material = _first_jsonl_record(
        baseline_dir / "Concept A" / "SS27" / "Brand A" / "materials.jsonl"
    )
    style_bom = _first_jsonl_record(
        baseline_dir / "Concept A" / "SS27" / "Brand A" / "style-boms.jsonl"
    )
    assert material["uom"] == "yd"
    assert style_bom["total_weight_kg"] == 1


def test_snapshot_diff_does_not_duplicate_owner_approval_for_changed_owner(
    tmp_path: Path,
) -> None:
    definition = SnapshotDefinition(name="dpp", title="DPP Snapshot")
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    _write_snapshot_artifact_set(
        baseline_dir,
        definition,
        style_weight=1,
        material_description="Plain weave",
        material_uom="m",
    )
    _write_snapshot_artifact_set(
        candidate_dir,
        definition,
        style_weight=2,
        material_description="Plain weave",
        material_uom="yd",
    )

    summary = diff_snapshot_artifacts(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        policy=_TestDppPolicy(),
    )

    assert [change.change_type for change in summary.changes] == [
        "field_changed",
        "record_changed",
    ]


def test_snapshot_diff_policy_can_hide_non_review_paths(tmp_path: Path) -> None:
    definition = SnapshotDefinition(name="demo", title="Demo Snapshot")
    group = ("Concept A",)
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    write_snapshot_artifacts(
        baseline_dir,
        definition=definition,
        output=SnapshotOutput(
            records=(
                SnapshotRecord(
                    stream="records",
                    key="R1",
                    group=group,
                    data={"_key": "R1", "value": 1, "debug": {"message": "old"}},
                ),
            )
        ),
        clean=True,
    )
    write_snapshot_artifacts(
        candidate_dir,
        definition=definition,
        output=SnapshotOutput(
            records=(
                SnapshotRecord(
                    stream="records",
                    key="R1",
                    group=group,
                    data={"_key": "R1", "value": 2, "debug": {"message": "new"}},
                ),
            )
        ),
        clean=True,
    )

    summary = diff_snapshot_artifacts(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        policy=_IgnoreDebugPolicy(),
    )

    assert [(change.path, change.old, change.new) for change in summary.changes] == [
        ("/value", 1, 2)
    ]


def test_snapshot_diff_policy_hides_record_change_when_only_ignored_paths_change(
    tmp_path: Path,
) -> None:
    definition = SnapshotDefinition(name="demo", title="Demo Snapshot")
    group = ("Concept A",)
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    write_snapshot_artifacts(
        baseline_dir,
        definition=definition,
        output=SnapshotOutput(
            records=(
                SnapshotRecord(
                    stream="records",
                    key="R1",
                    group=group,
                    data={"_key": "R1", "value": 1, "debug": {"message": "old"}},
                ),
            )
        ),
        clean=True,
    )
    write_snapshot_artifacts(
        candidate_dir,
        definition=definition,
        output=SnapshotOutput(
            records=(
                SnapshotRecord(
                    stream="records",
                    key="R1",
                    group=group,
                    data={"_key": "R1", "value": 1, "debug": {"message": "new"}},
                ),
            )
        ),
        clean=True,
    )

    summary = diff_snapshot_artifacts(
        definition=definition,
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        policy=_RecordIgnoreDebugPolicy(),
    )

    assert summary.changes == ()


def _write_snapshot_artifact_set(
    target_dir: Path,
    definition: SnapshotDefinition,
    *,
    style_weight: int,
    material_description: str,
    material_uom: str,
) -> None:
    group = ("Concept A", "SS27", "Brand A")
    output = SnapshotOutput(
        records=(
            SnapshotRecord(
                stream="style-boms",
                key="STYLE1|BOM1|style|",
                group=group,
                data={
                    "_key": "STYLE1|BOM1|style|",
                    "style": {"id": "STYLE1"},
                    "bom": {"id": "BOM1"},
                    "total_weight_kg": style_weight,
                    "materials": [
                        {
                            "material": {"id": "MAT1"},
                            "quantity": 1,
                            "weight_kg": style_weight,
                        }
                    ],
                },
            ),
            SnapshotRecord(
                stream="materials",
                key="MAT1",
                group=group,
                data={
                    "_key": "MAT1",
                    "id": "MAT1",
                    "description": material_description,
                    "uom": material_uom,
                },
            ),
        )
    )
    write_snapshot_artifacts(target_dir, definition=definition, output=output, clean=True)


def _first_jsonl_record(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


class _TestDppPolicy:
    record_promotion_streams = frozenset({"style-boms"})

    def locked_field_reason(
        self,
        identity: SnapshotRecordIdentity,
        path: str,
    ) -> str | None:
        if identity.stream == "materials" and path == "/uom":
            return "BOM-impacting material field; approve through affected style-boms."
        return None

    def approval_owner(
        self,
        identity: SnapshotRecordIdentity,
        path: str | None,
    ) -> str | None:
        if identity.stream == "materials" and path == "/uom":
            return "style-boms"
        return identity.stream

    def impacts(
        self,
        change: SnapshotChange,
        baseline,
        candidate,
    ) -> tuple[SnapshotRecordIdentity, ...]:
        if change.identity.stream != "materials" or change.path != "/uom":
            return ()
        records = {**baseline.records, **candidate.records}
        return tuple(
            identity
            for identity, record in sorted(records.items())
            if identity.stream == "style-boms"
            and any(
                row.get("material", {}).get("id") == change.identity.key
                for row in record.data.get("materials", [])
                if isinstance(row, dict)
            )
        )


class _IgnoreDebugPolicy:
    def ignored_change_path(
        self,
        _identity: SnapshotRecordIdentity,
        path: str,
    ) -> bool:
        return path.startswith("/debug")


class _RecordIgnoreDebugPolicy(_IgnoreDebugPolicy):
    record_promotion_streams = frozenset({"records"})
