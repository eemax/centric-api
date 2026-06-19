from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..config import ConfigError
from .common import (
    RawObservation,
    _iter_completed_run_paths,
    _manifest_endpoints_dict,
    _optional_string,
    iter_raw_index,
    load_raw_manifest,
)


def find_raw_observations(
    *,
    endpoint: str,
    record_id: str,
    raw_root: Path,
) -> list[RawObservation]:
    observations: list[RawObservation] = []
    for run_path in _iter_completed_run_paths(raw_root):
        manifest = load_raw_manifest(run_path)
        run_id = str(manifest.get("run_id") or run_path.name)
        run_started_at = str(manifest.get("started_at") or "")
        endpoint_record = _manifest_endpoints_dict(manifest).get(endpoint)
        if not isinstance(endpoint_record, dict):
            continue
        file_name = endpoint_record.get("file")
        index_name = endpoint_record.get("index_file")
        if not isinstance(file_name, str) or not isinstance(index_name, str):
            continue
        raw_file = run_path / file_name
        index_file = run_path / index_name
        if not index_file.is_file():
            continue
        for record in iter_raw_index(index_file):
            if str(record.get("record_id")) != record_id:
                continue
            observations.append(
                RawObservation(
                    endpoint=endpoint,
                    record_id=record_id,
                    run_id=run_id,
                    run_started_at=run_started_at,
                    raw_file=raw_file,
                    index_file=index_file,
                    line=int(record["line"]),
                    payload_sha256=str(record["payload_sha256"]),
                    raw_line_sha256=str(record["raw_line_sha256"]),
                    modified_at=_optional_string(record.get("modified_at")),
                    delete_type=_optional_string(record.get("delete_type")),
                    manifest_path=run_path / "manifest.json",
                )
            )
    return sorted(observations, key=_observation_sort_key)


def load_observation_payload(observation: RawObservation) -> dict[str, Any]:
    for line_number, raw_line in enumerate(observation.raw_file.open("rb"), start=1):
        if line_number != observation.line:
            continue
        return _parse_observation_payload(raw_line, observation, line_number)
    raise ConfigError(f"Raw observation line not found: {observation.raw_file}:{observation.line}")


def load_observation_payloads(
    observations: list[RawObservation],
) -> dict[RawObservation, dict[str, Any]]:
    grouped: dict[Path, list[RawObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.raw_file].append(observation)

    payloads: dict[RawObservation, dict[str, Any]] = {}
    for raw_file, file_observations in grouped.items():
        by_line = {observation.line: observation for observation in file_observations}
        with raw_file.open("rb") as fh:
            for line_number, raw_line in enumerate(fh, start=1):
                observation = by_line.pop(line_number, None)
                if observation is None:
                    continue
                payloads[observation] = _parse_observation_payload(
                    raw_line,
                    observation,
                    line_number,
                )
                if not by_line:
                    break
        if by_line:
            missing = min(by_line)
            raise ConfigError(f"Raw observation line not found: {raw_file}:{missing}")
    return payloads


def _parse_observation_payload(
    raw_line: bytes,
    observation: RawObservation,
    line_number: int,
) -> dict[str, Any]:
    payload = json.loads(raw_line.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ConfigError(f"Raw observation is not an object: {observation.raw_file}:{line_number}")
    return payload


def diff_payloads(left: Any, right: Any) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    _diff_payloads(left, right, path="", changes=changes)
    return changes


def choose_diff_observations(
    observations: list[RawObservation],
    *,
    from_hash: str | None = None,
    to_hash: str | None = None,
) -> tuple[RawObservation, RawObservation]:
    if from_hash or to_hash:
        left = _latest_matching_hash(observations, from_hash, label="--from")
        right = _latest_matching_hash(observations, to_hash, label="--to")
        if left is None or right is None:
            raise ConfigError("Both --from and --to hashes are required for explicit raw diff.")
        return left, right
    distinct: list[RawObservation] = []
    seen: set[str] = set()
    for observation in reversed(observations):
        if observation.payload_sha256 in seen:
            continue
        seen.add(observation.payload_sha256)
        distinct.append(observation)
        if len(distinct) == 2:
            break
    if len(distinct) < 2:
        raise ConfigError("Need at least two distinct raw payload hashes to diff.")
    return distinct[1], distinct[0]


def _observation_sort_key(observation: RawObservation) -> tuple[str, str, str, int]:
    return (
        observation.modified_at or "",
        observation.run_started_at,
        observation.run_id,
        observation.line,
    )


def _latest_matching_hash(
    observations: list[RawObservation],
    payload_hash: str | None,
    *,
    label: str,
) -> RawObservation | None:
    if not payload_hash:
        return None
    matches = [item for item in observations if item.payload_sha256.startswith(payload_hash)]
    if not matches:
        raise ConfigError(f"No raw observation matched {label} hash: {payload_hash}")
    if len({item.payload_sha256 for item in matches}) > 1:
        raise ConfigError(f"{label} hash is ambiguous: {payload_hash}")
    return matches[-1]


def _diff_payloads(left: Any, right: Any, *, path: str, changes: list[dict[str, Any]]) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child_path = f"{path}.{key}" if path else str(key)
            if key not in left:
                changes.append({"path": child_path, "from": None, "to": right[key]})
            elif key not in right:
                changes.append({"path": child_path, "from": left[key], "to": None})
            else:
                _diff_payloads(left[key], right[key], path=child_path, changes=changes)
        return
    if left != right:
        changes.append({"path": path or "$", "from": left, "to": right})
