from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ..load_config import LoadColumn
from .models import LoadIssue


def _parse_composition_entries(
    column: LoadColumn,
    raw_value: Any,
    row_number: int,
) -> list[tuple[Decimal, str]] | LoadIssue:
    text = str(raw_value).strip()
    entries = _composition_entries_from_text(text)
    if isinstance(entries, LoadIssue):
        return LoadIssue(
            row=row_number,
            code=entries.code,
            column=column.key,
            message=entries.message,
            sample=raw_value,
        )
    total = sum((percentage for percentage, _name in entries), Decimal("0"))
    if abs(total - Decimal("100")) > Decimal("0.0001"):
        return LoadIssue(
            row=row_number,
            code="composition_total_invalid",
            column=column.key,
            message=f"Composition total must be 100; got {_decimal_label(total)}.",
            sample=raw_value,
        )
    return entries


def _composition_entries_from_text(text: str) -> list[tuple[Decimal, str]] | LoadIssue:
    cleaned = text.strip().strip(".")
    if not cleaned:
        return LoadIssue(row=None, code="empty_composition", message="Composition is blank.")
    segments = [segment.strip() for segment in re.split(r"[,;+\n]+", cleaned) if segment.strip()]
    if len(segments) > 1:
        return _composition_entries_from_segments(segments)
    entries = _composition_entries_from_numeric_tokens(cleaned)
    if entries is not None:
        return entries
    return LoadIssue(
        row=None,
        code="composition_percentage_missing",
        message=f"Composition entry is missing a percentage: {cleaned!r}.",
    )


def _composition_entries_from_segments(
    segments: list[str],
) -> list[tuple[Decimal, str]] | LoadIssue:
    entries: list[tuple[Decimal, str]] = []
    for segment in segments:
        segment_entries = _composition_entries_from_numeric_tokens(segment)
        if isinstance(segment_entries, LoadIssue):
            return segment_entries
        if segment_entries is None:
            return LoadIssue(
                row=None,
                code="composition_percentage_missing",
                message=f"Composition entry is missing a percentage: {segment!r}.",
            )
        entries.extend(segment_entries)
    return entries


def _composition_entries_from_numeric_tokens(
    text: str,
) -> list[tuple[Decimal, str]] | LoadIssue | None:
    numbers = list(re.finditer(r"\d+(?:\.\d+)?\s*%?", text))
    if not numbers:
        return None

    percentages: list[Decimal] = []
    for number in numbers:
        percentage = _decimal_or_none(number.group().strip().rstrip("%"))
        if percentage is None:
            return LoadIssue(
                row=None,
                code="invalid_composition_percentage",
                message=f"Composition percentage must be numeric: {number.group()!r}.",
            )
        if percentage <= 0:
            return LoadIssue(
                row=None,
                code="invalid_composition_percentage",
                message=f"Composition percentage must be greater than 0: {number.group()!r}.",
            )
        percentages.append(percentage)

    parts = [text[: numbers[0].start()]]
    parts.extend(
        text[current.end() : next_number.start()]
        for current, next_number in zip(numbers, numbers[1:], strict=False)
    )
    parts.append(text[numbers[-1].end() :])
    names = [_clean_composition_name(part) for part in parts]
    if not any(names):
        return LoadIssue(
            row=None,
            code="composition_name_missing",
            message=f"Composition entry is missing a name: {text!r}.",
        )

    def assign(
        index: int,
        used_name_indexes: frozenset[int],
        entries: tuple[tuple[Decimal, str], ...],
    ) -> tuple[tuple[Decimal, str], ...] | None:
        if index == len(percentages):
            if all(
                not name or part_index in used_name_indexes for part_index, name in enumerate(names)
            ):
                return entries
            return None

        options: list[int] = []
        before_index = index
        after_index = index + 1
        if names[before_index] and before_index not in used_name_indexes:
            options.append(before_index)
        if names[after_index] and after_index not in used_name_indexes:
            options.append(after_index)

        for name_index in options:
            result = assign(
                index + 1,
                used_name_indexes | frozenset({name_index}),
                entries + ((percentages[index], names[name_index]),),
            )
            if result is not None:
                return result
        return None

    assigned = assign(0, frozenset(), ())
    if assigned is None:
        return LoadIssue(
            row=None,
            code="composition_name_missing",
            message=f"Composition entry is missing a name: {text!r}.",
        )
    return list(assigned)


def _clean_composition_name(value: str) -> str:
    cleaned = re.sub(r"^[\s,;/+._%-]+|[\s,;/+._%-]+$", "", value.strip())
    return re.sub(r"\s+", " ", cleaned).strip()


def _decimal_or_none(value: str) -> Decimal | None:
    try:
        return Decimal(value.strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


def _decimal_label(value: Decimal) -> str:
    normalized = value.normalize()
    return str(int(normalized)) if normalized == normalized.to_integral_value() else str(normalized)
