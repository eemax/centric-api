from __future__ import annotations

import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .bronze import PageCounts, apply_page, mark_deleted, seen_count, unseen_ids
from .client import AuthError, CentricClient, FetchError, load_credentials, query_params
from .config import ConfigError, EndpointSpec, Settings
from .db import RunLock, connect, iso, next_change_seq, transaction, utc_now
from .silver import SilverResult, sync_endpoint
from .swagger import SwaggerRefresh, load_specs, refresh_specs

COUNT_API_VERSION = "v2"
MODIFIED_AT_FIELD = "_modified_at"
VERIFY_BATCH_SIZE = 50
# An incremental window that cannot be proven complete is read again, up to this many passes.
MAX_DELTA_PASSES = 3

Progress = Callable[[str], None]


class IntegrityError(FetchError):
    """The server misbehaved while paging; the next run starts the endpoint over."""


@dataclass
class EndpointOutcome:
    endpoint: str
    mode: str
    status: str = "ok"  # "ok", "warn" or "failed"
    delta_floor: str | None = None
    resumed: bool = False
    expected: int | None = None
    fetched: int = 0
    counts: PageCounts = field(default_factory=PageCounts)
    deleted: int = 0
    missing: int = 0
    passes: int = 1
    complete: bool = False
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""
    seconds: float = 0.0


@dataclass
class FetchSummary:
    run_id: str
    status: str  # "ok", "warn", "partial" or "failed"
    endpoints: list[EndpointOutcome]
    swagger: list[SwaggerRefresh]
    silver: list[SilverResult]
    error: str | None = None


def run_fetch(
    settings: Settings,
    *,
    full: bool = False,
    endpoint_names: list[str] | None = None,
    restart: bool = False,
    allow_delete: bool = False,
    progress: Progress | None = None,
    client: CentricClient | None = None,
) -> FetchSummary:
    if allow_delete and not full:
        raise ConfigError("--allow-delete only applies to --full fetches.")
    selected = select_endpoints(settings.endpoints, endpoint_names)
    emit = progress or (lambda _message: None)
    conn = connect(settings.database)
    try:
        lock = RunLock(conn)
        with lock:
            owned_client = client is None
            if client is None:
                client = CentricClient(
                    load_credentials(settings.env_file),
                    settings.http,
                    on_retry=lambda message: emit(f"  retry: {message}"),
                )
            try:
                return _run(
                    conn,
                    lock,
                    client,
                    settings,
                    selected,
                    full=full,
                    restart=restart,
                    allow_delete=allow_delete,
                    emit=emit,
                )
            finally:
                if owned_client:
                    client.close()
    finally:
        conn.close()


def select_endpoints(
    endpoints: tuple[EndpointSpec, ...], names: list[str] | None
) -> list[EndpointSpec]:
    if not names:
        return list(endpoints)
    known = {spec.name for spec in endpoints}
    missing = sorted(set(names) - known)
    if missing:
        raise ConfigError(f"Unknown endpoint(s): {', '.join(missing)}")
    wanted = set(names)
    return [spec for spec in endpoints if spec.name in wanted]


def _run(
    conn: sqlite3.Connection,
    lock: RunLock,
    client: CentricClient,
    settings: Settings,
    selected: list[EndpointSpec],
    *,
    full: bool,
    restart: bool,
    allow_delete: bool,
    emit: Progress,
) -> FetchSummary:
    run_id = uuid.uuid4().hex
    command = " ".join(
        [
            "fetch",
            *(["--full"] if full else []),
            *(["--allow-delete"] if allow_delete else []),
            *(["--restart"] if restart else []),
            *(f"-e {spec.name}" for spec in selected if len(selected) < len(settings.endpoints)),
        ]
    )
    with transaction(conn):
        conn.execute(
            "INSERT INTO meta_runs (run_id, command, started_at, status) VALUES (?, ?, ?, ?)",
            [run_id, command, iso(utc_now()), "running"],
        )
    outcomes: list[EndpointOutcome] = []
    swagger_results: list[SwaggerRefresh] = []
    silver_results: list[SilverResult] = []
    fatal: str | None = None
    try:
        emit("swagger: refreshing")
        swagger_results = refresh_specs(conn, client, {spec.api_version for spec in selected})
        for item in swagger_results:
            emit(f"swagger {item.api_version}: {item.status}" + _suffix(item.message))
        for spec in selected:
            outcome = _fetch_endpoint(
                conn,
                lock,
                client,
                spec,
                settings,
                run_id=run_id,
                full=full,
                restart=restart,
                allow_delete=allow_delete,
                emit=emit,
            )
            outcomes.append(outcome)
        specs = load_specs(conn)
        for spec in selected:
            with transaction(conn):
                lock.heartbeat()
            result = sync_endpoint(conn, spec, specs)
            silver_results.append(result)
            emit(_silver_line(result))
    except AuthError as exc:
        fatal = f"authentication failed: {exc}"
        emit(f"error: {fatal}")
    except BaseException as exc:
        with transaction(conn):
            conn.execute(
                "UPDATE meta_runs SET finished_at = ?, status = 'failed', error = ? "
                "WHERE run_id = ?",
                [iso(utc_now()), f"{type(exc).__name__}: {exc}", run_id],
            )
        raise
    status = _run_status(outcomes, selected, fatal)
    with transaction(conn):
        conn.execute(
            "UPDATE meta_runs SET finished_at = ?, status = ?, error = ? WHERE run_id = ?",
            [iso(utc_now()), status, fatal, run_id],
        )
    return FetchSummary(run_id, status, outcomes, swagger_results, silver_results, fatal)


@dataclass
class _Window:
    """One endpoint's fetch window: the span being read and where reading has got to."""

    run_id: str
    mode: str
    started_at: str
    delta_floor: str | None
    pass_number: int
    skip: int
    fetched: int
    resumed: bool
    # Records modified at or after this time changed while the window was being read. It is the
    # window start less the overlap, so a Centric clock running behind cannot hide a change.
    changed_since: str = ""


def _fetch_endpoint(
    conn: sqlite3.Connection,
    lock: RunLock,
    client: CentricClient,
    spec: EndpointSpec,
    settings: Settings,
    *,
    run_id: str,
    full: bool,
    restart: bool,
    allow_delete: bool,
    emit: Progress,
) -> EndpointOutcome:
    started = time.monotonic()
    now = utc_now()
    state = _endpoint_state(conn, spec.name)
    window, clock_warning = _open_window(
        state, settings, run_id=run_id, full=full, restart=restart, now=now
    )
    outcome = EndpointOutcome(
        spec.name,
        window.mode,
        delta_floor=window.delta_floor,
        resumed=window.resumed,
        started_at=iso(now),
    )
    if clock_warning:
        outcome.warnings.append(clock_warning)
    span = f"since {window.delta_floor}" if window.delta_floor else "all records"
    emit(
        f"{spec.name}: {window.mode} ({span})"
        + (f", resuming pass {window.pass_number} at {window.skip:,}" if window.resumed else "")
    )
    with transaction(conn):
        conn.execute(
            "UPDATE meta_endpoint_state SET last_attempt_started_at = ?, "
            "last_attempt_status = 'running', last_attempt_error = NULL WHERE endpoint = ?",
            [iso(now), spec.name],
        )
        _save_position(conn, spec.name, window)

    try:
        params = query_params(spec.query_params)
        if window.delta_floor is not None:
            params.append((MODIFIED_AT_FIELD, f"ge{window.delta_floor}"))
        first_fetch = state["last_success_started_at"] is None
        # Re-reading is how an unproven window gets settled. Incremental windows are small, so
        # they are always re-read; a full window is only re-read on an endpoint's first fetch,
        # when there is no earlier cursor to fall back to.
        verify = window.mode == "delta" or first_fetch
        complete = _read_window(conn, lock, client, spec, window, params, outcome, emit, verify)
        found, missing = [], []
        if window.mode == "full":
            found, missing = _confirm_unseen(conn, lock, client, spec, window, outcome)
        # An unproven window may have skipped a record. Its data is kept, but the cursor stays
        # where it was so the next incremental run reads that span again. The first fetch of an
        # endpoint has no earlier cursor to fall back to, so it always moves forward.
        advance = complete or first_fetch
        if not complete and not advance and outcome.warnings:
            outcome.warnings.append("the incremental cursor was not moved forward")
        _finish_window(conn, lock, spec, window, outcome, found, missing, allow_delete, advance)
        outcome.complete = complete
        outcome.status = "warn" if outcome.warnings else "ok"
    except AuthError as exc:
        outcome.status = "failed"
        outcome.error = f"authentication failed: {exc}"
        outcome.seconds = time.monotonic() - started
        outcome.finished_at = iso(utc_now())
        _record_failure(conn, spec.name, outcome.error, keep_pending=True)
        _record_outcome(conn, run_id, outcome)
        raise
    except FetchError as exc:
        outcome.status = "failed"
        outcome.error = str(exc)
        _record_failure(conn, spec.name, str(exc), keep_pending=not isinstance(exc, IntegrityError))
        emit(f"  {spec.name}: FAILED {exc}")
    outcome.seconds = time.monotonic() - started
    outcome.finished_at = iso(utc_now())
    _record_outcome(conn, run_id, outcome)
    if outcome.status != "failed":
        counts = outcome.counts
        emit(
            f"  {spec.name}: {outcome.status} fetched={outcome.fetched:,} "
            f"new={counts.inserted:,} changed={counts.updated:,} restored={counts.restored:,} "
            f"deleted={outcome.deleted:,}"
            + (f" missing={outcome.missing:,}" if outcome.missing else "")
            + (f" passes={outcome.passes}" if outcome.passes > 1 else "")
        )
        if outcome.missing:
            emit(
                f"  {spec.name}: {outcome.missing:,} stored records no longer exist in Centric; "
                "run `fetch --full --allow-delete` to flag them deleted"
            )
        for note in outcome.notes:
            emit(f"  {spec.name}: note: {note}")
        for warning in outcome.warnings:
            emit(f"  {spec.name}: warning: {warning}")
    return outcome


def _open_window(
    state: sqlite3.Row,
    settings: Settings,
    *,
    run_id: str,
    full: bool,
    restart: bool,
    now: datetime,
) -> tuple[_Window, str | None]:
    """Resume the endpoint's unfinished window, or open a new one.

    An unfinished full window also satisfies an incremental request, since it covers more.
    """
    requested = "full" if full else "delta"
    pending = state["pending_run_id"] is not None and not restart
    if pending and state["pending_mode"] in {requested, "full"}:
        pass_number = int(state["pending_pass"] or 1)
        return (
            _Window(
                run_id=state["pending_run_id"],
                mode=state["pending_mode"],
                started_at=state["pending_started_at"],
                delta_floor=state["pending_delta_floor"],
                pass_number=pass_number,
                # A verification pass restarts from the top: it must see the whole window.
                skip=int(state["pending_next_skip"]) if pass_number == 1 else 0,
                fetched=int(state["pending_fetched"]) if pass_number == 1 else 0,
                resumed=True,
                changed_since=_changed_since(state["pending_started_at"], settings),
            ),
            None,
        )
    delta_floor = None
    clock_warning = None
    if requested == "delta" and state["last_success_started_at"]:
        last_start = _parse_iso(state["last_success_started_at"])
        if last_start > now:
            clock_warning = (
                f"the last successful fetch started at {state['last_success_started_at']}, which "
                "is in the future; the system clock moved back, so a full fetch was taken instead"
            )
        else:
            delta_floor = iso(last_start - timedelta(minutes=settings.overlap_minutes))
    # Without a usable cursor (first fetch, or a clock problem) take a complete snapshot.
    mode = requested if (requested == "full" or delta_floor is not None) else "full"
    started_at = iso(now)
    window = _Window(
        run_id, mode, started_at, delta_floor, 1, 0, 0, False, _changed_since(started_at, settings)
    )
    return window, clock_warning


def _changed_since(started_at: str, settings: Settings) -> str:
    return iso(_parse_iso(started_at) - timedelta(minutes=settings.overlap_minutes))


def _read_window(
    conn: sqlite3.Connection,
    lock: RunLock,
    client: CentricClient,
    spec: EndpointSpec,
    window: _Window,
    params: list[tuple[str, Any]],
    outcome: EndpointOutcome,
    emit: Progress,
    verify: bool,
) -> bool:
    """Read the window and return whether it is proven complete.

    Pass 1 is proven when every record is distinct and at least the count was fetched; extra
    records were created while paging. Otherwise a record may have been skipped, and with
    `verify` the window is read again. A further pass proves it complete when it finds nothing
    the earlier passes missed, or is itself distinct and complete. Agreement between passes also
    shows that a remaining count difference is records Centric counts but does not list.
    """
    expected = _expected_count(client, spec, params)
    outcome.expected = expected
    if window.pass_number == 1:
        first = _read_pass(conn, lock, client, spec, window, params, outcome, emit, expected)
        # Counted from the database so a pass resumed by a later run is judged as a whole.
        unique = seen_count(conn, endpoint=spec.name, run_id=window.run_id)
        # A record deleted mid-fetch makes the pages skip one record; a record created at the
        # same time hides that in the totals. The created record is visible as a concurrent
        # change, so an incremental pass that saw one is checked by a second pass.
        changed = first.concurrent > 0 or window.resumed
        if unique == window.fetched and unique >= expected:
            if not changed:
                return True
            if window.mode == "full" and not verify:
                # Cheaper than re-reading a full window: keep the cursor where it was, so the
                # next incremental run re-reads everything changed since then.
                outcome.notes.append(
                    "records changed during the full fetch, so the incremental cursor stays at "
                    "its previous position and the next incremental run re-reads from there"
                )
                return False
            if window.mode == "full":
                return True  # first fetch: re-reading everything is not worth it for this alone
        if unique < window.fetched:
            reason = (
                f"{window.fetched - unique:,} records appeared twice (records moved while paging)"
            )
        elif unique < expected:
            reason = f"fetched {unique:,} of the {expected:,} records Centric counted"
        elif window.resumed:
            reason = "the window was resumed from an earlier run"
        else:
            reason = f"{first.concurrent:,} records changed while the window was being read"
        if not verify:
            outcome.warnings.append(f"{reason}; the fetch could not be proven complete")
            return False
        window.pass_number = 2
        window.skip = window.fetched = 0
    else:
        reason = "the previous run's verification pass was interrupted"
    outcome.notes.append(f"{reason}; reading the window again")
    while window.pass_number <= MAX_DELTA_PASSES:
        outcome.passes = window.pass_number
        with transaction(conn):
            _save_position(conn, spec.name, window)
        result = _read_pass(conn, lock, client, spec, window, params, outcome, emit, expected)
        found = (
            f"found {result.late_finds:,} records the earlier passes missed"
            if result.late_finds
            else "found nothing the earlier passes missed"
        )
        if result.late_finds == 0 or (result.duplicates == 0 and result.distinct >= expected):
            outcome.notes.append(
                f"pass {window.pass_number} {found} and proved the window complete"
            )
            return True
        outcome.notes.append(f"pass {window.pass_number} {found}")
        window.pass_number += 1
        window.skip = window.fetched = 0
    outcome.warnings.append(
        f"the window could not be proven complete after {MAX_DELTA_PASSES} passes because "
        "records kept changing"
    )
    return False


@dataclass
class _PassResult:
    late_finds: int = 0
    duplicates: int = 0
    distinct: int = 0
    concurrent: int = 0


def _read_pass(
    conn: sqlite3.Connection,
    lock: RunLock,
    client: CentricClient,
    spec: EndpointSpec,
    window: _Window,
    params: list[tuple[str, Any]],
    outcome: EndpointOutcome,
    emit: Progress,
    expected: int,
) -> _PassResult:
    """Page through the window until Centric returns an empty page.

    The offset advances by the records actually received, so a server that returns fewer
    records than asked for never causes a gap, and records created while paging are collected
    rather than treated as an error.
    """
    page_params = [*params, ("sort", MODIFIED_AT_FIELD)]
    runaway_limit = 3 * expected + 100 * spec.limit
    result = _PassResult()
    seen: set[str] = set()
    previous_ids: list[str] | None = None
    while True:
        items = _get_page(client, spec, page_params, window.skip)
        if not items:
            result.distinct = len(seen)
            return result
        ids = [str(item.get("id")) if isinstance(item, dict) else "" for item in items]
        if ids == previous_ids:
            raise IntegrityError(
                f"{spec.name}: Centric returned the same page twice in a row; it is not "
                "honouring 'skip', so the endpoint cannot be paged."
            )
        previous_ids = ids
        result.duplicates += sum(1 for record_id in ids if record_id in seen)
        seen.update(ids)
        with transaction(conn):
            lock.heartbeat()
            page_counts = apply_page(
                conn,
                endpoint=spec.name,
                items=items,
                run_id=window.run_id,
                now=iso(utc_now()),
                change_seq=next_change_seq(conn),
                window_started_at=window.started_at if window.pass_number > 1 else None,
                changed_since=window.changed_since,
            )
            window.skip += len(items)
            window.fetched += len(items)
            _save_position(conn, spec.name, window)
        result.late_finds += page_counts.late_finds
        result.concurrent += page_counts.concurrent
        outcome.counts.add(page_counts)
        # Pass 1 counts the whole window, including pages read before a resume.
        if window.pass_number == 1:
            outcome.fetched = window.fetched
        else:
            outcome.fetched += len(items)
        label = f" pass {window.pass_number}" if window.pass_number > 1 else ""
        emit(f"  {spec.name}{label}: {window.fetched:,}/{expected:,}")
        if window.fetched > runaway_limit:
            raise IntegrityError(
                f"{spec.name}: received {window.fetched:,} records for a count of {expected:,} "
                "without reaching the end; stopping."
            )


def _confirm_unseen(
    conn: sqlite3.Connection,
    lock: RunLock,
    client: CentricClient,
    spec: EndpointSpec,
    window: _Window,
    outcome: EndpointOutcome,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Look up stored records a full fetch did not return; split into found and gone."""
    unseen = unseen_ids(conn, endpoint=spec.name, run_id=window.run_id)
    if not unseen:
        return [], []
    if outcome.fetched == 0:
        outcome.warnings.append(
            f"Centric returned no records but {len(unseen):,} are stored; "
            "nothing was flagged deleted."
        )
        return [], []
    found = _lookup_by_id(client, spec, unseen, lock=lock, conn=conn)
    if found:
        outcome.warnings.append(
            f"{len(found):,} records missing from the full fetch still exist in Centric "
            "(skipped while paging, or outside this endpoint's filters); they were stored."
        )
    found_ids = {str(item.get("id")) for item in found}
    return found, [record_id for record_id in unseen if record_id not in found_ids]


def _finish_window(
    conn: sqlite3.Connection,
    lock: RunLock,
    spec: EndpointSpec,
    window: _Window,
    outcome: EndpointOutcome,
    found: list[dict[str, Any]],
    missing: list[str],
    allow_delete: bool,
    advance: bool,
) -> None:
    with transaction(conn):
        lock.heartbeat()
        if found:
            outcome.counts.add(
                apply_page(
                    conn,
                    endpoint=spec.name,
                    items=found,
                    run_id=window.run_id,
                    now=iso(utc_now()),
                    change_seq=next_change_seq(conn),
                )
            )
        if missing and allow_delete:
            outcome.deleted = mark_deleted(
                conn,
                endpoint=spec.name,
                ids=missing,
                now=iso(utc_now()),
                change_seq=next_change_seq(conn),
            )
        elif missing:
            outcome.missing = len(missing)
        finished = iso(utc_now())
        if advance:
            conn.execute(
                "UPDATE meta_endpoint_state SET last_success_started_at = ? WHERE endpoint = ?",
                [window.started_at, spec.name],
            )
        conn.execute(
            """
            UPDATE meta_endpoint_state
            SET last_success_finished_at = ?, last_success_mode = ?,
                last_full_success_at = CASE WHEN ? = 'full' THEN ? ELSE last_full_success_at END,
                last_attempt_status = ?, last_attempt_error = NULL,
                pending_run_id = NULL, pending_mode = NULL, pending_pass = NULL,
                pending_started_at = NULL, pending_delta_floor = NULL, pending_next_skip = NULL,
                pending_fetched = NULL
            WHERE endpoint = ?
            """,
            [
                finished,
                window.mode,
                window.mode,
                finished,
                "warn" if outcome.warnings else "ok",
                spec.name,
            ],
        )


def _save_position(conn: sqlite3.Connection, endpoint: str, window: _Window) -> None:
    """Persist the window position. Call inside a transaction."""
    conn.execute(
        """
        UPDATE meta_endpoint_state
        SET pending_run_id = ?, pending_mode = ?, pending_pass = ?, pending_started_at = ?,
            pending_delta_floor = ?, pending_next_skip = ?, pending_fetched = ?
        WHERE endpoint = ?
        """,
        [
            window.run_id,
            window.mode,
            window.pass_number,
            window.started_at,
            window.delta_floor,
            window.skip,
            window.fetched,
            endpoint,
        ],
    )


def _expected_count(
    client: CentricClient, spec: EndpointSpec, params: list[tuple[str, Any]]
) -> int:
    try:
        payload = client.get_json(client.url(COUNT_API_VERSION, spec.count_path), params)
    except FetchError as exc:
        if exc.status_code == 404:
            raise FetchError(
                f"{spec.name}: count path '{spec.count_path}' does not exist on this Centric "
                "instance (HTTP 404). Fix it, or remove the endpoint from `endpoints:` in your "
                "config if your instance does not have this object type.",
                status_code=404,
            ) from exc
        raise
    value = payload.get("count") if isinstance(payload, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise FetchError(f"{spec.name}: count endpoint returned no valid 'count'.")
    if isinstance(value, float) and not value.is_integer():
        raise FetchError(f"{spec.name}: count endpoint returned a non-integer count.")
    return int(value)


def _get_page(
    client: CentricClient, spec: EndpointSpec, params: list[tuple[str, Any]], skip: int
) -> list[dict[str, Any]]:
    payload = client.get_json(
        client.url(spec.api_version, spec.path),
        [*params, ("skip", skip), ("limit", spec.limit)],
    )
    if not isinstance(payload, list):
        raise FetchError(f"{spec.name}: expected a JSON array of records.")
    return payload


def _lookup_by_id(
    client: CentricClient,
    spec: EndpointSpec,
    record_ids: list[str],
    *,
    lock: RunLock,
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Ask Centric directly for records a full fetch did not return.

    Endpoint filters are deliberately not applied: a record only counts as gone when it no
    longer exists in Centric at all. If the endpoint ignores the id filter, nothing can be
    confirmed, so the lookup fails instead of treating unreturned records as deleted.
    """
    found: list[dict[str, Any]] = []
    for start in range(0, len(record_ids), VERIFY_BATCH_SIZE):
        batch = record_ids[start : start + VERIFY_BATCH_SIZE]
        with transaction(conn):
            lock.heartbeat()
        payload = client.get_json(
            client.url(spec.api_version, spec.path),
            [
                *(("id", record_id) for record_id in batch),
                ("decoded", "true"),
                ("limit", len(batch)),
            ],
        )
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise FetchError(f"{spec.name}: expected a JSON array when looking up records by id.")
        wanted = set(batch)
        unexpected = [item.get("id") for item in payload if str(item.get("id")) not in wanted]
        if unexpected:
            raise FetchError(
                f"{spec.name}: looking up records by id returned other records "
                f"(e.g. {unexpected[0]!r}); this endpoint does not support id lookups, so "
                "missing records cannot be confirmed."
            )
        found.extend(payload)
    return found


def _endpoint_state(conn: sqlite3.Connection, endpoint: str) -> sqlite3.Row:
    with transaction(conn):
        conn.execute(
            "INSERT INTO meta_endpoint_state (endpoint) VALUES (?) ON CONFLICT DO NOTHING",
            [endpoint],
        )
        return conn.execute(
            "SELECT * FROM meta_endpoint_state WHERE endpoint = ?", [endpoint]
        ).fetchone()


def _record_failure(
    conn: sqlite3.Connection, endpoint: str, error: str, *, keep_pending: bool
) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE meta_endpoint_state SET last_attempt_status = 'failed', "
            "last_attempt_error = ? WHERE endpoint = ?",
            [error, endpoint],
        )
        if not keep_pending:
            conn.execute(
                """
                UPDATE meta_endpoint_state
                SET pending_run_id = NULL, pending_mode = NULL, pending_pass = NULL,
                    pending_started_at = NULL, pending_delta_floor = NULL,
                    pending_next_skip = NULL, pending_fetched = NULL
                WHERE endpoint = ?
                """,
                [endpoint],
            )


def _record_outcome(conn: sqlite3.Connection, run_id: str, outcome: EndpointOutcome) -> None:
    counts = outcome.counts
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO meta_run_endpoints (
                run_id, endpoint, mode, delta_floor, resumed, status, expected, fetched,
                inserted, updated, unchanged, restored, deleted, missing, passes, complete, note,
                warning, error, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                outcome.endpoint,
                outcome.mode,
                outcome.delta_floor,
                int(outcome.resumed),
                outcome.status,
                outcome.expected,
                outcome.fetched,
                counts.inserted,
                counts.updated,
                counts.unchanged,
                counts.restored,
                outcome.deleted,
                outcome.missing,
                outcome.passes,
                int(outcome.complete),
                "; ".join(outcome.notes) or None,
                "; ".join(outcome.warnings) or None,
                outcome.error,
                outcome.started_at,
                outcome.finished_at,
            ],
        )


def _run_status(
    outcomes: list[EndpointOutcome], selected: list[EndpointSpec], fatal: str | None
) -> str:
    failed = sum(1 for item in outcomes if item.status == "failed")
    if fatal or (selected and failed == len(selected)):
        return "failed"
    if failed:
        return "partial"
    if any(item.status == "warn" for item in outcomes):
        return "warn"
    return "ok"


def _silver_line(result: SilverResult) -> str:
    parts = [f"silver {result.endpoint}: {result.status}"]
    if result.status != "skipped":
        parts.append(f"rows={result.rows:,}")
    if result.unmapped:
        parts.append(f"unmapped={','.join(result.unmapped)}")
    parts.extend(result.notes)
    return " ".join(parts)


def _suffix(message: str | None) -> str:
    return f" ({message})" if message else ""


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
