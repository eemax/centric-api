from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .fetch_common import FetchError


@dataclass
class CheckpointState:
    exists: bool
    valid: bool
    next_skip: int = 0
    fetched_count: int = 0
    delta_floor: str | None = None
    completed: bool | None = None
    restart_from_zero: bool = False
    window_start_line: int | None = None
    output_file: Path | None = None
    invalid_reason: str | None = None


def read_checkpoint(path: Path) -> CheckpointState:
    if not path.is_file():
        return CheckpointState(exists=False, valid=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return CheckpointState(
            exists=True,
            valid=False,
            invalid_reason=f"file is not valid JSON ({exc})",
        )
    if not isinstance(payload, dict):
        return CheckpointState(
            exists=True,
            valid=False,
            invalid_reason="checkpoint root must be an object",
        )

    next_skip = payload.get("next_skip", 0)
    fetched_count = payload.get("fetched_count", 0)
    checkpoint_delta_floor = payload.get("delta_floor")
    completed = payload.get("completed")
    restart_from_zero = payload.get("restart_from_zero", False)
    window_start_line = payload.get("window_start_line")
    output_file = payload.get("output_file")

    if not isinstance(next_skip, int) or next_skip < 0:
        return CheckpointState(
            exists=True,
            valid=False,
            invalid_reason="next_skip must be a non-negative integer",
        )
    if not isinstance(fetched_count, int) or fetched_count < 0:
        return CheckpointState(
            exists=True,
            valid=False,
            invalid_reason="fetched_count must be a non-negative integer",
        )

    normalized_delta_floor: str | None = None
    if checkpoint_delta_floor is not None:
        if not isinstance(checkpoint_delta_floor, str):
            return CheckpointState(
                exists=True,
                valid=False,
                invalid_reason="delta_floor must be a string when present",
            )
        stripped = checkpoint_delta_floor.strip()
        normalized_delta_floor = stripped or None

    normalized_completed: bool | None
    if completed is None:
        normalized_completed = None
    elif isinstance(completed, bool):
        normalized_completed = completed
    else:
        return CheckpointState(
            exists=True,
            valid=False,
            invalid_reason="completed must be true/false when present",
        )

    if not isinstance(restart_from_zero, bool):
        return CheckpointState(
            exists=True,
            valid=False,
            invalid_reason="restart_from_zero must be true/false when present",
        )

    normalized_window_start_line: int | None
    if window_start_line is None:
        if next_skip > 0:
            return CheckpointState(
                exists=True,
                valid=False,
                invalid_reason="window_start_line is required when next_skip is greater than zero",
            )
        normalized_window_start_line = None
    elif isinstance(window_start_line, int) and window_start_line >= 0:
        normalized_window_start_line = window_start_line
    else:
        return CheckpointState(
            exists=True,
            valid=False,
            invalid_reason="window_start_line must be a non-negative integer when present",
        )

    normalized_output_file: Path | None = None
    if output_file is not None:
        if not isinstance(output_file, str) or not output_file.strip():
            return CheckpointState(
                exists=True,
                valid=False,
                invalid_reason="output_file must be a non-empty string when present",
            )
        normalized_output_file = Path(output_file.strip())

    return CheckpointState(
        exists=True,
        valid=True,
        next_skip=next_skip,
        fetched_count=fetched_count,
        delta_floor=normalized_delta_floor,
        completed=normalized_completed,
        restart_from_zero=restart_from_zero,
        window_start_line=normalized_window_start_line,
        output_file=normalized_output_file,
    )


def write_checkpoint(
    path: Path,
    endpoint: str,
    next_skip: int,
    fetched_count: int,
    *,
    delta_floor: str | None = None,
    completed: bool = False,
    restart_from_zero: bool = False,
    window_start_line: int | None = None,
    output_file: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "endpoint": endpoint,
        "next_skip": next_skip,
        "fetched_count": fetched_count,
        "updated_at": datetime.now(UTC).isoformat(),
        "completed": completed,
        "restart_from_zero": restart_from_zero,
    }
    if delta_floor is not None:
        payload["delta_floor"] = delta_floor
    if window_start_line is not None:
        payload["window_start_line"] = window_start_line
    if output_file is not None:
        payload["output_file"] = str(output_file)
    temp_path = path.parent / f".{path.name}.tmp"
    try:
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def count_file_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    with path.open("r", encoding="utf-8") as fh:
        for _ in fh:
            count += 1
    return count


def truncate_file_lines(path: Path, line_count: int) -> None:
    if line_count < 0:
        raise ValueError("line_count must be non-negative.")
    if not path.is_file():
        return
    if line_count == 0:
        with path.open("r+b") as fh:
            fh.truncate(0)
        return

    seen_lines = 0
    offset = 0
    truncate_at: int | None = None
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            for index, byte in enumerate(chunk):
                if byte != 0x0A:
                    continue
                seen_lines += 1
                if seen_lines == line_count:
                    truncate_at = offset + index + 1
                    break
            if truncate_at is not None:
                break
            offset += len(chunk)
    if truncate_at is not None:
        with path.open("r+b") as fh:
            fh.truncate(truncate_at)


def track_item_id(
    item: Any,
    *,
    seen_ids: set[Any],
    duplicate_id_set: set[Any],
    duplicate_ids: list[Any],
) -> str | None:
    if not isinstance(item, dict):
        return f"line value is non-object type {type(item).__name__}"
    if "id" not in item:
        return "missing field 'id'"

    item_id = item["id"]
    if item_id is None:
        return "field 'id' is null"
    try:
        hash(item_id)
    except TypeError:
        return f"field 'id' is unhashable type {type(item_id).__name__}"

    if item_id in seen_ids:
        if item_id not in duplicate_id_set:
            duplicate_id_set.add(item_id)
            duplicate_ids.append(item_id)
        return None

    seen_ids.add(item_id)
    return None


def seed_resume_window_id_state(
    output_path: Path,
    *,
    endpoint_name: str,
    window_start_line: int,
    fetched_count: int,
    seen_ids: set[Any],
    duplicate_id_set: set[Any],
    duplicate_ids: list[Any],
) -> tuple[int, int, str | None]:
    if fetched_count <= 0:
        return 0, 0, None
    if not output_path.is_file():
        raise FetchError(
            f"Output file missing for resume validation on '{endpoint_name}': {output_path}. "
            "Action: restart endpoint window."
        )

    start = window_start_line
    end = window_start_line + fetched_count
    loaded = 0
    invalid_id_count = 0
    first_invalid_detail: str | None = None
    with output_path.open("r", encoding="utf-8") as fh:
        for line_index, line in enumerate(fh):
            if line_index < start:
                continue
            if line_index >= end:
                break
            loaded += 1
            text = line.strip()
            if not text:
                invalid_id_count += 1
                if first_invalid_detail is None:
                    first_invalid_detail = f"line {line_index + 1} is empty"
                continue
            try:
                item = json.loads(text)
            except Exception as exc:
                raise FetchError(
                    "Invalid JSONL while validating resume checkpoint window "
                    f"for '{endpoint_name}' "
                    f"at line {line_index + 1}: {exc}. Action: restart endpoint window."
                ) from exc

            invalid_detail = track_item_id(
                item,
                seen_ids=seen_ids,
                duplicate_id_set=duplicate_id_set,
                duplicate_ids=duplicate_ids,
            )
            if invalid_detail is not None:
                invalid_id_count += 1
                if first_invalid_detail is None:
                    first_invalid_detail = f"line {line_index + 1}: {invalid_detail}"

    if loaded != fetched_count:
        raise FetchError(
            f"Resume checkpoint for '{endpoint_name}' expects {fetched_count} records "
            "in output window "
            f"starting at line {window_start_line + 1}, but found {loaded}. "
            "Action: restart endpoint window."
        )
    return loaded, invalid_id_count, first_invalid_detail
