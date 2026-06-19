from __future__ import annotations

COUNT_DRIFT_RELATIVE_TOLERANCE = 0.00025
COUNT_DRIFT_ABSOLUTE_CAP = 10


def is_acceptable_count_drift(
    *,
    items_fetched: int,
    expected_count: int,
    limit: int,
    last_page_items: int | None,
) -> bool:
    if expected_count <= 0 or items_fetched <= 0:
        return False
    if last_page_items is None or last_page_items >= limit:
        return False
    delta = abs(expected_count - items_fetched)
    if delta == 0:
        return False
    if delta > COUNT_DRIFT_ABSOLUTE_CAP:
        return False
    if delta > limit:
        return False
    return (delta / expected_count) <= COUNT_DRIFT_RELATIVE_TOLERANCE
