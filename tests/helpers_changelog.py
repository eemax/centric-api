from __future__ import annotations

from centric_api.store import PreviousRecord


def _previous_records(conn, pairs: list[tuple[str, str]]) -> dict[str, dict[str, PreviousRecord]]:
    records: dict[str, dict[str, PreviousRecord]] = {}
    for endpoint, record_id in pairs:
        row = conn.execute(
            """
            SELECT payload_json, payload_sha256
            FROM endpoint_records
            WHERE endpoint = ? AND record_id = ?
            """,
            [endpoint, record_id],
        ).fetchone()
        records.setdefault(endpoint, {})[record_id] = PreviousRecord(
            payload_hash=row[1],
            payload_json=row[0],
        )
    return records
