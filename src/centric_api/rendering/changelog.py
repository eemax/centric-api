from __future__ import annotations

from typing import Any

from ..time_display import format_time_ago
from .common import format_count, signed_count


def print_human_changelog_summary(
    rows: list[dict[str, Any]],
    actor_rows: list[dict[str, Any]],
    *,
    since: str | None,
    endpoint: str | None,
    limit: int,
) -> None:
    endpoint_rows = _changelog_endpoint_rows(rows)
    displayed_rows = endpoint_rows[:limit]
    totals = _changelog_totals(endpoint_rows)

    print("Centric API Changelog")
    print()
    print(f"Since:    {_changelog_since_label(since)}")
    if endpoint:
        print(f"Endpoint: {endpoint}")
    print(
        f"Events:   {format_count(totals['total'])} across "
        f"{len(endpoint_rows)} endpoint{'' if len(endpoint_rows) == 1 else 's'}"
    )
    print()
    print("Totals")
    print(f"  Added:        {format_count(totals['added'])}")
    print(f"  Changed:      {format_count(totals['changed'])}")
    if totals["removed"]:
        removed_parts = [f"{format_count(totals['removed'])} total"]
        if totals["tombstone"]:
            removed_parts.append(f"{format_count(totals['tombstone'])} tombstoned")
        if totals["hard_delete"]:
            removed_parts.append(f"{format_count(totals['hard_delete'])} hard-deleted")
        if totals["unknown_delete"]:
            removed_parts.append(f"{format_count(totals['unknown_delete'])} unknown")
        print(f"  Removed:      {', '.join(removed_parts)}")
    else:
        print("  Removed:      0")

    print()
    print("Endpoints")
    name_width = max(len("Endpoint"), *(len(row["endpoint"]) for row in displayed_rows))
    header = (
        f"  {'Endpoint':<{name_width}}  {'Added':>8}  {'Changed':>8}  {'Removed':>9}  {'Total':>8}"
    )
    print(header)
    print(f"  {'-' * (len(header) - 2)}")
    for row in displayed_rows:
        print(
            f"  {row['endpoint']:<{name_width}}  "
            f"{signed_count('+', row['added']):>8}  "
            f"{signed_count('~', row['changed']):>8}  "
            f"{_removed_label(row):>9}  "
            f"{format_count(row['total']):>8}"
        )
    hidden_count = len(endpoint_rows) - len(displayed_rows)
    if hidden_count > 0:
        print(f"  ... {hidden_count} more endpoint{'' if hidden_count == 1 else 's'}")

    if actor_rows:
        print()
        print("Modified By")
        name_width = max(len("Actor"), *(_actor_label_width(row) for row in actor_rows))
        for row in actor_rows:
            print(
                f"  {_actor_label(row):<{name_width}}  "
                f"{format_count(int(row['count'] or 0)):>8} changes"
            )


def print_human_changelog_runs(rows: list[dict[str, Any]], *, since: str | None) -> None:
    print("Changelog Runs")
    print()
    print(f"Since: {_changelog_since_label(since)}")
    print(f"Runs:  {format_count(len(rows))}")
    print()
    run_width = max(len("Run"), *(len(str(row["run_id"])) for row in rows))
    header = (
        f"{'Run':<{run_width}}  {'Created':<12}  {'Endpoints':>9}  "
        f"{'Records':>9}  {'Events':>8}  Mode"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        mode = "full" if row["full_refresh"] else "delta"
        print(
            f"{str(row['run_id']):<{run_width}}  "
            f"{format_time_ago(row['created_at']):<12}  "
            f"{format_count(int(row['endpoint_count'] or 0)):>9}  "
            f"{format_count(int(row['record_count'] or 0)):>9}  "
            f"{format_count(int(row['event_count'] or 0)):>8}  "
            f"{mode}"
        )


def print_human_changelog_field_summary(
    rows: list[dict[str, Any]],
    change_rows: list[dict[str, Any]],
    *,
    since: str | None,
    endpoint: str | None,
) -> None:
    field_rows = _changelog_field_rows(rows)
    print("Changelog Fields")
    print()
    print(f"Since:    {_changelog_since_label(since)}")
    if endpoint:
        print(f"Endpoint: {endpoint}")
        print("Detail:   field-event counts")
        print(f"Fields:   {format_count(len(field_rows))}")
        print()
        _print_changelog_field_detail_table(field_rows)
        return
    endpoint_rows = _changelog_field_endpoint_rows(field_rows, change_rows)
    print(f"Endpoints: {format_count(len(endpoint_rows))}")
    print()
    endpoint_width = max(len("Endpoint"), *(len(row["endpoint"]) for row in endpoint_rows))
    header = (
        f"{'Endpoint':<{endpoint_width}}  {'Added Rec':>9}  {'Changed Rec':>11}  "
        f"{'Removed Rec':>11}  {'Fields':>8}  {'Changes':>8}  Top changed fields"
    )
    print(header)
    print("-" * len(header))
    for row in endpoint_rows:
        print(
            f"{row['endpoint']:<{endpoint_width}}  "
            f"{format_count(row['added_records']):>9}  "
            f"{format_count(row['changed_records']):>11}  "
            f"{format_count(row['removed_records']):>11}  "
            f"{format_count(row['changed_field_count']):>8}  "
            f"{format_count(row['changed_field_events']):>8}  "
            f"{row['top_fields']}"
        )


def print_human_changelog_actor_summary(
    rows: list[dict[str, Any]],
    *,
    since: str | None,
    endpoint: str | None,
) -> None:
    actor_rows = _changelog_actor_rows(rows)
    print("Changelog Actors")
    print()
    print(f"Since:    {_changelog_since_label(since)}")
    if endpoint:
        print(f"Endpoint: {endpoint}")
    print(f"Actors:   {format_count(len(actor_rows))}")
    print()
    actor_width = max(len("Actor"), *(_actor_label_width(row) for row in actor_rows))
    endpoint_width = max(len("Endpoint"), *(len(row["endpoint"]) for row in actor_rows))
    header = (
        f"{'Actor':<{actor_width}}  {'Endpoint':<{endpoint_width}}  "
        f"{'Added':>8}  {'Changed':>8}  {'Removed':>8}  {'Total':>8}"
    )
    print(header)
    print("-" * len(header))
    for row in actor_rows:
        print(
            f"{_actor_label(row):<{actor_width}}  "
            f"{row['endpoint']:<{endpoint_width}}  "
            f"{format_count(row['added']):>8}  "
            f"{format_count(row['changed']):>8}  "
            f"{format_count(row['removed']):>8}  "
            f"{format_count(row['total']):>8}"
        )


def print_human_changelog_changes(
    rows: list[dict[str, Any]],
    *,
    since: str | None,
    endpoint: str | None,
) -> None:
    print("Changelog Changes")
    print()
    print(f"Detected since: {_changelog_since_label(since)}")
    if endpoint:
        print(f"Endpoint: {endpoint}")
    print(f"Changes:  {format_count(len(rows))}")
    print()
    rows_for_width = [{**row, "fields_label": _change_fields_label(row)} for row in rows]
    endpoint_width = max(len("Endpoint"), *(len(str(row["endpoint"])) for row in rows))
    record_width = max(len("Record"), *(len(str(row["record_id"])) for row in rows))
    type_width = max(len("Type"), *(_change_type_width(row) for row in rows))
    actor_width = max(len("Actor"), *(_actor_label_width(row) for row in rows))
    header = (
        f"{'Modified':<12}  {'Endpoint':<{endpoint_width}}  "
        f"{'Record':<{record_width}}  {'Type':<{type_width}}  "
        f"{'Actor':<{actor_width}}  Fields"
    )
    print(header)
    print("-" * len(header))
    for row in rows_for_width:
        print(
            f"{format_time_ago(row.get('modified_at') or row['changed_at']):<12}  "
            f"{str(row['endpoint']):<{endpoint_width}}  "
            f"{str(row['record_id']):<{record_width}}  "
            f"{_change_type_label(row):<{type_width}}  "
            f"{_actor_label(row):<{actor_width}}  "
            f"{row['fields_label']}"
        )


def _changelog_endpoint_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    endpoints: dict[str, dict[str, Any]] = {}
    for row in rows:
        endpoint = str(row["endpoint"])
        endpoint_row = endpoints.setdefault(
            endpoint,
            {
                "endpoint": endpoint,
                "added": 0,
                "changed": 0,
                "removed": 0,
                "tombstone": 0,
                "hard_delete": 0,
                "unknown_delete": 0,
                "total": 0,
            },
        )
        count = int(row["count"] or 0)
        change_type = row["change_type"]
        delete_type = row.get("delete_type")
        if change_type == "added":
            endpoint_row["added"] += count
        elif change_type == "changed":
            endpoint_row["changed"] += count
        elif change_type == "removed":
            endpoint_row["removed"] += count
            if delete_type == "tombstone":
                endpoint_row["tombstone"] += count
            elif delete_type == "hard_delete":
                endpoint_row["hard_delete"] += count
            else:
                endpoint_row["unknown_delete"] += count
        endpoint_row["total"] += count
    return sorted(endpoints.values(), key=lambda row: (-int(row["total"]), row["endpoint"]))


def _changelog_field_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["endpoint"]), str(row["field"]))
        field_row = fields.setdefault(
            key,
            {
                "endpoint": key[0],
                "field": key[1],
                "added": 0,
                "changed": 0,
                "removed": 0,
                "total": 0,
            },
        )
        count = int(row["count"] or 0)
        field_change_type = row["field_change_type"]
        if field_change_type == "added_field":
            field_row["added"] += count
        elif field_change_type == "changed_field":
            field_row["changed"] += count
        elif field_change_type == "removed_field":
            field_row["removed"] += count
        field_row["total"] += count
    return sorted(
        fields.values(),
        key=lambda row: (-int(row["total"]), row["endpoint"], row["field"]),
    )


def _print_changelog_field_detail_table(field_rows: list[dict[str, Any]]) -> None:
    endpoint_width = max(len("Endpoint"), *(len(row["endpoint"]) for row in field_rows))
    field_width = max(len("Field"), *(len(row["field"]) for row in field_rows))
    header = (
        f"{'Endpoint':<{endpoint_width}}  {'Field':<{field_width}}  "
        f"{'Added':>8}  {'Changed':>8}  {'Removed':>8}  {'Total':>8}"
    )
    print(header)
    print("-" * len(header))
    for row in field_rows:
        print(
            f"{row['endpoint']:<{endpoint_width}}  "
            f"{row['field']:<{field_width}}  "
            f"{format_count(row['added']):>8}  "
            f"{format_count(row['changed']):>8}  "
            f"{format_count(row['removed']):>8}  "
            f"{format_count(row['total']):>8}"
        )


def _changelog_field_endpoint_rows(
    field_rows: list[dict[str, Any]],
    change_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    endpoints: dict[str, dict[str, Any]] = {}
    changed_fields: dict[str, list[dict[str, Any]]] = {}
    for row in field_rows:
        endpoint = str(row["endpoint"])
        endpoint_row = endpoints.setdefault(endpoint, _empty_field_endpoint_row(endpoint))
        if int(row["changed"]) and not _is_metadata_field(row["field"]):
            endpoint_row["changed_field_count"] += 1
            endpoint_row["changed_field_events"] += int(row["changed"])
            changed_fields.setdefault(endpoint, []).append(row)
    _apply_endpoint_record_counts(endpoints, change_rows)
    for endpoint, endpoint_row in endpoints.items():
        changed = changed_fields.get(endpoint, [])
        top = sorted(
            changed,
            key=lambda row: (-int(row["changed"]), str(row["field"])),
        )[:5]
        endpoint_row["top_fields"] = ", ".join(str(row["field"]) for row in top) or "none"
    return sorted(
        endpoints.values(),
        key=lambda row: (
            -int(row["changed_field_events"]),
            -int(row["added_records"]),
            row["endpoint"],
        ),
    )


def _apply_endpoint_record_counts(
    endpoints: dict[str, dict[str, Any]],
    change_rows: list[dict[str, Any]],
) -> None:
    for row in change_rows:
        endpoint = str(row["endpoint"])
        endpoint_row = endpoints.setdefault(endpoint, _empty_field_endpoint_row(endpoint))
        change_type = row["change_type"]
        count = int(row["count"] or 0)
        if change_type == "added":
            endpoint_row["added_records"] += count
        elif change_type == "changed":
            endpoint_row["changed_records"] += count
        elif change_type == "removed":
            endpoint_row["removed_records"] += count


def _empty_field_endpoint_row(endpoint: str) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "added_records": 0,
        "changed_records": 0,
        "removed_records": 0,
        "changed_field_count": 0,
        "changed_field_events": 0,
        "top_fields": "",
    }


def _changelog_actor_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actors: dict[tuple[str | None, str | None, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("modified_by_id"), row.get("modified_by_name"), str(row["endpoint"]))
        actor_row = actors.setdefault(
            key,
            {
                "modified_by_id": key[0],
                "modified_by_name": key[1],
                "endpoint": key[2],
                "added": 0,
                "changed": 0,
                "removed": 0,
                "total": 0,
            },
        )
        count = int(row["count"] or 0)
        change_type = row["change_type"]
        if change_type == "added":
            actor_row["added"] += count
        elif change_type == "changed":
            actor_row["changed"] += count
        elif change_type == "removed":
            actor_row["removed"] += count
        actor_row["total"] += count
    return sorted(
        actors.values(),
        key=lambda row: (-int(row["total"]), _actor_label(row), row["endpoint"]),
    )


def _changelog_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    keys = [
        "added",
        "changed",
        "removed",
        "tombstone",
        "hard_delete",
        "unknown_delete",
        "total",
    ]
    return {key: sum(int(row[key]) for row in rows) for key in keys}


def _removed_label(row: dict[str, Any]) -> str:
    removed = int(row["removed"])
    if not removed:
        return ""
    if row["hard_delete"] and not row["tombstone"] and not row["unknown_delete"]:
        return signed_count("-", removed, suffix=" hard")
    return signed_count("-", removed)


def _change_type_label(row: dict[str, Any]) -> str:
    if row["change_type"] != "removed":
        return str(row["change_type"])
    delete_type = row.get("delete_type")
    if delete_type:
        return f"removed/{delete_type}"
    return "removed"


def _change_type_width(row: dict[str, Any]) -> int:
    return len(_change_type_label(row))


def _change_fields_label(row: dict[str, Any]) -> str:
    value = row.get("changed_fields")
    if row["change_type"] == "removed":
        return "record removed"
    if not isinstance(value, list) or not value:
        return "none"
    if row["change_type"] == "added" and len(value) > 8:
        return f"{format_count(len(value))} fields added"
    non_metadata = [str(item) for item in value if not _is_metadata_field(item)]
    display_fields = non_metadata or [str(item) for item in value]
    visible = display_fields[:5]
    suffix = (
        f", ...+{len(display_fields) - len(visible)} more"
        if len(display_fields) > len(visible)
        else ""
    )
    return f"{', '.join(visible)}{suffix}"


def _is_metadata_field(value: Any) -> bool:
    return str(value) in {"_modified_at", "modified_by"}


def _actor_label(row: dict[str, Any]) -> str:
    return str(row.get("modified_by_name") or row.get("modified_by_id") or "Unknown")


def _actor_label_width(row: dict[str, Any]) -> int:
    return len(_actor_label(row))


def _changelog_since_label(value: str | None) -> str:
    if value is None or not value.strip():
        return "all time"
    return value.strip()
