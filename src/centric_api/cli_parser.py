from __future__ import annotations

import argparse
import sys

from .defaults import (
    DEFAULT_CONFIG_PATH,
    MAX_DAYS_BACK,
    MAX_MONTHS_BACK,
    MIN_DAYS_BACK,
    MIN_MONTHS_BACK,
)
from .rendering.logs import LOG_LEVEL_RANKS


def normalize_argv(argv: list[str] | None) -> list[str] | None:
    args = sys.argv[1:] if argv is None else list(argv)
    if args[:1] != ["bundle"]:
        return argv
    if len(args) == 1:
        return ["bundle", "run"]
    next_arg = args[1]
    bundle_actions = {"run", "list", "show", "changelog"}
    if next_arg not in bundle_actions and next_arg not in {"-h", "--help"}:
        return [args[0], "run", *args[1:]]
    return args


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="centric-api")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Fetch Centric API records")
    fetch_parser.add_argument(
        "--fetch-config",
        metavar="PATH",
        default=str(DEFAULT_CONFIG_PATH),
        help="Fetcher config path.",
    )
    fetch_parser.add_argument(
        "--endpoint",
        metavar="NAME",
        action="append",
        default=[],
        help="Endpoint to fetch; repeat for multiple endpoints.",
    )
    fetch_parser.add_argument("--full", action="store_true", help="Refetch all records.")
    fetch_parser.add_argument(
        "--days",
        metavar="N",
        type=_parse_days_back,
        default=None,
        help="Fetch records modified in the last N days.",
    )
    fetch_parser.add_argument(
        "--months",
        metavar="N",
        type=_parse_months_back,
        default=None,
        help="Fetch records modified in the last N calendar months.",
    )
    fetch_parser.add_argument("--resume", action="store_true", help="Resume from checkpoints.")
    fetch_parser.add_argument("--db", metavar="PATH", default=None, help="SQLite database path.")
    fetch_parser.add_argument(
        "--schema",
        metavar="PATH",
        default=None,
        help="Endpoint schema config path.",
    )
    fetch_parser.add_argument(
        "--delta-state-file",
        metavar="PATH",
        default=None,
        help="Delta state file path.",
    )
    fetch_parser.add_argument(
        "--delta-dry-run",
        action="store_true",
        help="Print derived delta filters without fetching.",
    )
    fetch_parser.add_argument(
        "--env-file", metavar="PATH", default=None, help="Credential env file."
    )
    fetch_parser.add_argument("--quiet", action="store_true", help="Suppress human progress.")
    fetch_parser.add_argument("--json", action="store_true", help="Emit JSON Lines output.")
    fetch_parser.add_argument(
        "--log-level",
        choices=list(LOG_LEVEL_RANKS),
        default="summary",
        help="Fetch log verbosity.",
    )

    ingest_parser = subparsers.add_parser("ingest", help="Inspect or ingest raw evidence")
    ingest_actions = ingest_parser.add_subparsers(dest="action", required=True)

    ingest_check_parser = ingest_actions.add_parser("check", help="Validate a raw run")
    ingest_check_parser.add_argument("raw_run", metavar="RAW_RUN", help="Raw run id or path.")
    ingest_check_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    ingest_check_parser.add_argument(
        "--schema",
        metavar="PATH",
        default=None,
        help="Endpoint schema config path.",
    )
    ingest_check_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    ingest_raw_run_parser = ingest_actions.add_parser("raw-run", help="Ingest one raw run")
    ingest_raw_run_parser.add_argument("raw_run", metavar="RAW_RUN", help="Raw run id or path.")
    ingest_raw_run_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    ingest_raw_run_parser.add_argument(
        "--schema",
        metavar="PATH",
        default=None,
        help="Endpoint schema config path.",
    )
    ingest_raw_run_parser.add_argument(
        "--changelog",
        action="store_true",
        help="Run the normal scoped changelog after ingest.",
    )
    ingest_raw_run_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    changelog_parser = subparsers.add_parser("changelog", help="Inspect or update changelog")
    changelog_parser.add_argument(
        "action",
        nargs="?",
        choices=["summary", "fields", "actors", "leaderboard", "runs", "changes", "update"],
        default="summary",
        help="Changelog action to run.",
    )
    changelog_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    changelog_parser.add_argument(
        "--endpoint",
        metavar="NAME",
        action="append",
        default=[],
        help="Endpoint filter. Repeatable for update; read views accept one endpoint.",
    )
    changelog_parser.add_argument(
        "--since",
        metavar="VALUE",
        default=None,
        help="Relative window like 7d/24h/10m or an ISO timestamp.",
    )
    changelog_parser.add_argument(
        "--limit",
        metavar="N",
        type=_parse_positive_int,
        default=50,
        help="Row limit.",
    )
    changelog_parser.add_argument("--json", action="store_true", help="Emit JSON output.")

    download_parser = subparsers.add_parser("download", help="Download latest document revisions")
    download_parser.add_argument(
        "--download-config",
        metavar="PATH",
        default=None,
        help="Download config path.",
    )
    download_parser.add_argument("--job", metavar="NAME", default=None, help="Download job name.")
    download_parser.add_argument("--db", metavar="PATH", default=None, help="SQLite database path.")
    download_parser.add_argument(
        "--fetch-config",
        metavar="PATH",
        default=str(DEFAULT_CONFIG_PATH),
        help="Fetcher config path for credentials.",
    )
    download_parser.add_argument(
        "--env-file", metavar="PATH", default=None, help="Credential env file."
    )
    download_parser.add_argument("--dry-run", action="store_true", help="Plan without downloading.")
    download_mode = download_parser.add_mutually_exclusive_group()
    download_mode.add_argument("--sync", action="store_true", help="Verify selected current files.")
    download_mode.add_argument("--rebuild", action="store_true", help="Redownload selected files.")
    download_parser.add_argument("--quiet", action="store_true", help="Suppress human output.")
    download_parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    download_parser.add_argument(
        "--log-level",
        choices=list(LOG_LEVEL_RANKS),
        default="summary",
        help="Download log verbosity.",
    )

    bundle_parser = subparsers.add_parser("bundle", help="Package downloaded files into a bundle")
    bundle_actions = bundle_parser.add_subparsers(dest="action", required=True)

    bundle_run_parser = bundle_actions.add_parser("run", help="Package downloaded files")
    bundle_run_parser.add_argument(
        "--bundle-config", metavar="PATH", default=None, help="Bundle config path."
    )
    bundle_run_parser.add_argument("--job", metavar="NAME", default=None, help="Bundle job name.")
    bundle_run_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    bundle_run_parser.add_argument(
        "--dry-run", action="store_true", help="Plan without writing bundle state."
    )
    bundle_run_parser.add_argument(
        "--no-zip", action="store_true", help="Skip ZIP archive creation."
    )
    bundle_run_parser.add_argument("--quiet", action="store_true", help="Suppress human output.")
    bundle_run_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    bundle_list_parser = bundle_actions.add_parser("list", help="List bundle runs")
    bundle_list_parser.add_argument(
        "--job", metavar="NAME", default=None, help="Bundle job filter."
    )
    bundle_list_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    bundle_list_parser.add_argument("--limit", metavar="N", type=int, default=50, help="Run limit.")
    bundle_list_parser.add_argument("--json", action="store_true", help="Emit JSON Lines output.")

    bundle_show_parser = bundle_actions.add_parser("show", help="Show one bundle run")
    bundle_show_parser.add_argument("bundle_run_id", metavar="RUN_ID", help="Bundle run id.")
    bundle_show_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    bundle_show_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    bundle_changelog_parser = bundle_actions.add_parser(
        "changelog",
        help="Compare a received bundle run with a later run",
    )
    bundle_changelog_parser.add_argument(
        "bundle_run_id", metavar="RUN_ID", help="Source bundle run id."
    )
    bundle_changelog_parser.add_argument(
        "--to", metavar="RUN_ID", default="latest", help="Comparison target run."
    )
    bundle_changelog_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    bundle_changelog_parser.add_argument(
        "--json", action="store_true", help="Emit one JSON object."
    )

    view_parser = subparsers.add_parser("view", help="Export configured tabular cache views")
    view_actions = view_parser.add_subparsers(dest="action", required=True)

    view_list_parser = view_actions.add_parser("list", help="List configured views")
    view_list_parser.add_argument(
        "--view-config", metavar="PATH", default=None, help="View config path."
    )
    view_list_parser.add_argument("--json", action="store_true", help="Emit JSON Lines output.")

    view_show_parser = view_actions.add_parser("show", help="Show one configured view")
    view_show_parser.add_argument("name", metavar="NAME", help="View name.")
    view_show_parser.add_argument(
        "--view-config", metavar="PATH", default=None, help="View config path."
    )
    view_show_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    view_check_parser = view_actions.add_parser("check", help="Check a configured view")
    view_check_parser.add_argument("name", metavar="NAME", help="View name.")
    view_check_parser.add_argument(
        "--view-config", metavar="PATH", default=None, help="View config path."
    )
    view_check_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    view_check_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    view_export_parser = view_actions.add_parser("export", help="Export a configured view")
    view_export_parser.add_argument("name", metavar="NAME", help="View name.")
    view_export_parser.add_argument(
        "--view-config", metavar="PATH", default=None, help="View config path."
    )
    view_export_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    view_export_parser.add_argument(
        "--format",
        choices=["xlsx", "csv"],
        default=None,
        help="Export format; inferred from --output when omitted.",
    )
    view_export_parser.add_argument(
        "--output", metavar="PATH", default=None, help="Output file path."
    )
    view_export_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    load_parser = subparsers.add_parser("load", help="Load spreadsheet rows into Centric API")
    load_parser.add_argument(
        "--load-config", metavar="PATH", default=None, help="Load config path."
    )
    load_actions = load_parser.add_subparsers(dest="action", required=True)

    load_list_parser = load_actions.add_parser("list", help="List configured load jobs")
    _add_load_config_override(load_list_parser)
    load_list_parser.add_argument("--json", action="store_true", help="Emit JSON Lines output.")

    load_show_parser = load_actions.add_parser("show", help="Show one load job")
    load_show_parser.add_argument("name", metavar="NAME", help="Load job name.")
    _add_load_config_override(load_show_parser)
    load_show_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    load_check_parser = load_actions.add_parser("check", help="Validate a load workbook")
    load_check_parser.add_argument("name", metavar="NAME", help="Load job name.")
    load_check_parser.add_argument("workbook", metavar="WORKBOOK", help="Input workbook path.")
    _add_load_config_override(load_check_parser)
    load_check_parser.add_argument("--sheet", metavar="NAME", default=None, help="Worksheet name.")
    load_check_parser.add_argument(
        "--limit",
        metavar="N",
        type=_parse_positive_int,
        default=None,
        help="Process only the first N data rows.",
    )
    load_check_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    load_check_parser.add_argument("--quiet", action="store_true", help="Suppress human progress.")
    load_check_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    load_run_parser = load_actions.add_parser("run", help="Run a load job")
    load_run_parser.add_argument("name", metavar="NAME", help="Load job name.")
    load_run_parser.add_argument("workbook", metavar="WORKBOOK", help="Input workbook path.")
    _add_load_config_override(load_run_parser)
    load_run_parser.add_argument("--sheet", metavar="NAME", default=None, help="Worksheet name.")
    load_run_parser.add_argument(
        "--limit",
        metavar="N",
        type=_parse_positive_int,
        default=None,
        help="Process only the first N data rows.",
    )
    load_run_parser.add_argument("--db", metavar="PATH", default=None, help="SQLite database path.")
    load_run_parser.add_argument(
        "--env-file", metavar="PATH", default=None, help="Credential env file."
    )
    load_run_parser.add_argument(
        "--dry-run", action="store_true", help="Write artifacts without API calls."
    )
    load_run_parser.add_argument("--yes", action="store_true", help="Confirm real API writes.")
    load_run_parser.add_argument("--quiet", action="store_true", help="Suppress human progress.")
    load_run_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    load_retry_parser = load_actions.add_parser(
        "retry", help="Retry failed rows from a review workbook"
    )
    load_retry_parser.add_argument("name", metavar="NAME", help="Load job name.")
    load_retry_parser.add_argument(
        "workbook",
        metavar="REVIEW_WORKBOOK",
        help="Review workbook path.",
    )
    _add_load_config_override(load_retry_parser)
    load_retry_parser.add_argument("--sheet", metavar="NAME", default=None, help="Worksheet name.")
    load_retry_parser.add_argument(
        "--statuses",
        metavar="LIST",
        default=None,
        help="Comma-separated review statuses to retry.",
    )
    load_retry_parser.add_argument(
        "--limit",
        metavar="N",
        type=_parse_positive_int,
        default=None,
        help="Process only the first N matching data rows.",
    )
    load_retry_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    load_retry_parser.add_argument(
        "--env-file", metavar="PATH", default=None, help="Credential env file."
    )
    load_retry_parser.add_argument(
        "--dry-run", action="store_true", help="Write artifacts without API calls."
    )
    load_retry_parser.add_argument("--yes", action="store_true", help="Confirm real API writes.")
    load_retry_parser.add_argument("--quiet", action="store_true", help="Suppress human progress.")
    load_retry_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    model_parser = subparsers.add_parser("model", help="Run private calculated data models")
    model_parser.add_argument(
        "--models-dir", metavar="PATH", default=None, help="Private models directory."
    )
    model_parser.add_argument(
        "--units-config", metavar="PATH", default=None, help="Units config path."
    )
    model_actions = model_parser.add_subparsers(dest="action", required=True)

    model_list_parser = model_actions.add_parser("list", help="List available models")
    _add_model_config_overrides(model_list_parser)
    model_list_parser.add_argument("--json", action="store_true", help="Emit JSON Lines output.")

    model_show_parser = model_actions.add_parser("show", help="Show one model")
    model_show_parser.add_argument("name", metavar="NAME", help="Model name.")
    _add_model_config_overrides(model_show_parser)
    model_show_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    model_check_parser = model_actions.add_parser("check", help="Check one model")
    model_check_parser.add_argument("name", metavar="NAME", help="Model name.")
    _add_model_config_overrides(model_check_parser)
    model_check_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    model_check_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    model_run_parser = model_actions.add_parser("run", help="Run one model")
    model_run_parser.add_argument("name", metavar="NAME", help="Model name.")
    _add_model_config_overrides(model_run_parser)
    model_run_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    model_run_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    validate_parser = subparsers.add_parser(
        "validate",
        help="Run cache validation reports",
    )
    validate_parser.add_argument(
        "--validators-dir",
        metavar="PATH",
        default=None,
        help="Private validators directory.",
    )
    validate_parser.add_argument(
        "--units-config",
        metavar="PATH",
        default=None,
        help="Units config path.",
    )
    validate_actions = validate_parser.add_subparsers(dest="action", required=True)

    validate_list_parser = validate_actions.add_parser("list", help="List available validators")
    _add_validate_config_overrides(validate_list_parser)
    validate_list_parser.add_argument("--json", action="store_true", help="Emit JSON Lines output.")

    validate_show_parser = validate_actions.add_parser("show", help="Show one validator")
    validate_show_parser.add_argument("name", metavar="NAME", help="Validator name.")
    _add_validate_config_overrides(validate_show_parser)
    validate_show_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    validate_run_parser = validate_actions.add_parser("run", help="Run one validator or all")
    validate_run_parser.add_argument(
        "name",
        metavar="NAME",
        help="Validator name, or all.",
    )
    _add_validate_config_overrides(validate_run_parser)
    validate_run_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    validate_run_parser.add_argument(
        "--output-dir",
        metavar="PATH",
        default=None,
        help="Validation artifact root directory.",
    )
    validate_run_parser.add_argument(
        "--mode",
        choices=("cache", "excel"),
        default=None,
        help="Validation mode. Default: excel when --input-file is supplied, otherwise cache.",
    )
    validate_run_parser.add_argument(
        "--input-file",
        metavar="PATH",
        default=None,
        help="Optional input workbook for validators that support external files.",
    )
    validate_run_parser.add_argument("--json", action="store_true", help="Emit JSON Lines output.")

    validate_history_parser = validate_actions.add_parser(
        "history",
        help="Refresh validation history artifacts from run history.json files",
    )
    validate_history_parser.add_argument(
        "--group",
        choices=("day", "week", "month"),
        default="week",
        help="Time bucket for grouped history points. Default: week.",
    )
    validate_history_parser.add_argument(
        "--validator",
        metavar="NAME",
        action="append",
        default=[],
        help="Filter to one validator; repeat for multiple validators.",
    )
    validate_history_parser.add_argument(
        "--runs-dir",
        metavar="PATH",
        default=None,
        help="Validation runs root. Default: CENTRIC_API_HOME/validation/runs.",
    )
    validate_history_parser.add_argument(
        "--output-dir",
        metavar="PATH",
        default=None,
        help="History output directory. Default: CENTRIC_API_HOME/validation/history.",
    )
    validate_history_parser.add_argument(
        "--json", action="store_true", help="Emit one JSON object."
    )

    snapshot_parser = subparsers.add_parser(
        "snapshot",
        help="Build private modeled JSONL snapshots",
    )
    snapshot_parser.add_argument(
        "--snapshots-dir",
        metavar="PATH",
        default=None,
        help="Private snapshots directory.",
    )
    snapshot_parser.add_argument(
        "--units-config",
        metavar="PATH",
        default=None,
        help="Units config path.",
    )
    snapshot_actions = snapshot_parser.add_subparsers(dest="action", required=True)

    snapshot_list_parser = snapshot_actions.add_parser("list", help="List available snapshots")
    _add_snapshot_dir_override(snapshot_list_parser)
    snapshot_list_parser.add_argument("--json", action="store_true", help="Emit JSON Lines output.")

    snapshot_show_parser = snapshot_actions.add_parser("show", help="Show one snapshot")
    snapshot_show_parser.add_argument("name", metavar="NAME", help="Snapshot name.")
    _add_snapshot_dir_override(snapshot_show_parser)
    snapshot_show_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    snapshot_check_parser = snapshot_actions.add_parser("check", help="Check one snapshot")
    snapshot_check_parser.add_argument("name", metavar="NAME", help="Snapshot name.")
    _add_snapshot_config_overrides(snapshot_check_parser)
    snapshot_check_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    snapshot_check_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    snapshot_build_parser = snapshot_actions.add_parser("build", help="Build one snapshot")
    snapshot_build_parser.add_argument("name", metavar="NAME", help="Snapshot name.")
    _add_snapshot_config_overrides(snapshot_build_parser)
    snapshot_build_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    snapshot_build_parser.add_argument(
        "--output-dir",
        metavar="PATH",
        default=None,
        help="Snapshot workspace root directory.",
    )
    snapshot_build_parser.add_argument(
        "--target",
        choices=["candidate", "baseline"],
        default="candidate",
        help="Snapshot workspace target. Default: candidate.",
    )
    snapshot_build_parser.add_argument(
        "--clean",
        action="store_true",
        help="Replace non-hidden contents in the selected snapshot target.",
    )
    snapshot_build_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    snapshot_promote_parser = snapshot_actions.add_parser(
        "promote",
        help="Promote candidate snapshot artifacts to baseline",
    )
    snapshot_promote_parser.add_argument("name", metavar="NAME", help="Snapshot name.")
    _add_snapshot_dir_override(snapshot_promote_parser)
    snapshot_promote_parser.add_argument(
        "--output-dir",
        metavar="PATH",
        default=None,
        help="Snapshot workspace root directory.",
    )
    snapshot_promote_parser.add_argument(
        "--clean",
        action="store_true",
        help="Replace non-hidden contents in the baseline target.",
    )
    snapshot_promote_parser.add_argument(
        "--json", action="store_true", help="Emit one JSON object."
    )

    map_parser = subparsers.add_parser("map", help="Generate local cache relationship maps")
    map_actions = map_parser.add_subparsers(dest="action", required=True)

    map_endpoints_parser = map_actions.add_parser(
        "endpoints",
        help="Map inferred endpoint references from the local cache",
    )
    map_endpoints_parser.add_argument(
        "--db", metavar="PATH", default=None, help="SQLite database path."
    )
    map_endpoints_parser.add_argument(
        "--output-dir",
        metavar="PATH",
        default=None,
        help="Endpoint map artifact root directory.",
    )
    map_endpoints_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    units_parser = subparsers.add_parser("units", help="Inspect and convert configured units")
    units_parser.add_argument(
        "--units-config", metavar="PATH", default=None, help="Units config path."
    )
    units_actions = units_parser.add_subparsers(dest="action", required=True)

    units_list_parser = units_actions.add_parser("list", help="List unit dimensions")
    _add_units_config_override(units_list_parser)
    units_list_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    units_show_parser = units_actions.add_parser("show", help="Show one unit dimension")
    units_show_parser.add_argument("dimension", metavar="DIMENSION", help="Unit dimension.")
    _add_units_config_override(units_show_parser)
    units_show_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    units_normalize_parser = units_actions.add_parser("normalize", help="Normalize a unit label")
    units_normalize_parser.add_argument("unit", metavar="UNIT", help="Unit label or alias.")
    _add_units_config_override(units_normalize_parser)
    units_normalize_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    units_convert_parser = units_actions.add_parser("convert", help="Convert a value between units")
    units_convert_parser.add_argument("value", metavar="VALUE", help="Numeric value.")
    units_convert_parser.add_argument("from_unit", metavar="FROM_UNIT", help="Source unit.")
    units_convert_parser.add_argument("to_unit", metavar="TO_UNIT", help="Target unit.")
    _add_units_config_override(units_convert_parser)
    units_convert_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    units_basis_parser = units_actions.add_parser(
        "basis",
        help="Show how a material unit drives consumption math",
    )
    units_basis_parser.add_argument("unit", metavar="UNIT", help="Material unit label or alias.")
    _add_units_config_override(units_basis_parser)
    units_basis_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    units_check_parser = units_actions.add_parser("check", help="Validate unit registry")
    _add_units_config_override(units_check_parser)
    units_check_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    cron_parser = subparsers.add_parser("cron", help="Run scheduled delta fetches in foreground")
    cron_parser.add_argument(
        "schedule",
        nargs="?",
        default="0 * * * *",
        help="Five-field cron schedule.",
    )
    cron_parser.add_argument("--run-now", action="store_true", help="Run one fetch immediately.")
    cron_parser.add_argument(
        "--endpoint",
        metavar="NAME",
        action="append",
        default=[],
        help="Endpoint to fetch; repeat for multiple endpoints.",
    )
    cron_parser.add_argument(
        "--fetch-config",
        metavar="PATH",
        default=str(DEFAULT_CONFIG_PATH),
        help="Fetcher config path.",
    )
    cron_parser.add_argument("--db", metavar="PATH", default=None, help="SQLite database path.")
    cron_parser.add_argument(
        "--schema", metavar="PATH", default=None, help="Endpoint schema config path."
    )
    cron_parser.add_argument(
        "--delta-state-file",
        metavar="PATH",
        default=None,
        help="Delta state file path.",
    )
    cron_parser.add_argument(
        "--env-file", metavar="PATH", default=None, help="Credential env file."
    )

    status_parser = subparsers.add_parser("status", help="Show local Centric API status")
    status_parser.add_argument("--db", metavar="PATH", default=None, help="SQLite database path.")
    status_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    swagger_parser = subparsers.add_parser("swagger", help="Inspect local Centric Swagger schema")
    swagger_actions = swagger_parser.add_subparsers(dest="action", required=True)

    swagger_refresh_parser = swagger_actions.add_parser("refresh", help="Fetch Swagger JSON")
    swagger_refresh_parser.add_argument(
        "--fetch-config",
        metavar="PATH",
        default=str(DEFAULT_CONFIG_PATH),
        help="Fetcher config path for credentials.",
    )
    swagger_refresh_parser.add_argument(
        "--env-file", metavar="PATH", default=None, help="Credential env file."
    )
    swagger_refresh_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    swagger_status_parser = swagger_actions.add_parser("status", help="Show Swagger freshness")
    swagger_status_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    swagger_history_parser = swagger_actions.add_parser(
        "history",
        help="List local Swagger history snapshots",
    )
    swagger_history_parser.add_argument(
        "--diffs",
        action="store_true",
        help="Show adjacent history diff counts instead of snapshot rows.",
    )
    swagger_history_parser.add_argument(
        "--json", action="store_true", help="Emit JSON Lines output."
    )

    swagger_endpoints_parser = swagger_actions.add_parser(
        "endpoints",
        help="List Swagger paths and methods",
    )
    swagger_endpoints_parser.add_argument(
        "--endpoint", metavar="NAME", default=None, help="Filter by root endpoint."
    )
    swagger_endpoints_parser.add_argument(
        "--json", action="store_true", help="Emit JSON Lines output."
    )

    swagger_fields_parser = swagger_actions.add_parser(
        "fields",
        help="List Swagger request and response fields",
    )
    swagger_fields_parser.add_argument(
        "--endpoint", metavar="NAME", default=None, help="Filter by root endpoint."
    )
    swagger_fields_parser.add_argument(
        "--method",
        choices=["get", "post", "put", "patch", "delete", "all"],
        default="get",
        help="HTTP method to inspect. Default: get.",
    )
    swagger_fields_parser.add_argument(
        "--include-nested",
        action="store_true",
        help="Include nested/action paths under the endpoint.",
    )
    swagger_fields_parser.add_argument(
        "--required-only", action="store_true", help="Only show required fields."
    )
    swagger_fields_parser.add_argument(
        "--json", action="store_true", help="Emit JSON Lines output."
    )

    swagger_field_parser = swagger_actions.add_parser(
        "field",
        help="Inspect one Swagger field without truncating enum values",
    )
    swagger_field_parser.add_argument(
        "selector",
        metavar="INDEX_OR_FIELD",
        help="Global field index, or field name when --endpoint is passed.",
    )
    swagger_field_parser.add_argument(
        "--endpoint",
        metavar="NAME",
        default=None,
        help="Filter by root endpoint and interpret selector as a field name.",
    )
    swagger_field_parser.add_argument(
        "--method",
        choices=["get", "post", "put", "patch", "delete", "all"],
        default="all",
        help="HTTP method to inspect. Default: all.",
    )
    swagger_field_parser.add_argument(
        "--include-nested",
        action="store_true",
        help="Include nested/action paths under the endpoint.",
    )
    swagger_field_parser.add_argument("--json", action="store_true", help="Emit JSON Lines output.")

    swagger_diff_parser = swagger_actions.add_parser("diff", help="Show Swagger schema drift")
    swagger_diff_source = swagger_diff_parser.add_mutually_exclusive_group()
    swagger_diff_source.add_argument(
        "--against",
        metavar="PATH",
        default=None,
        help="Compare local Swagger against another Swagger JSON file.",
    )
    swagger_diff_source.add_argument(
        "--history",
        metavar=("CURRENT_INDEX", "BASELINE_INDEX"),
        nargs=2,
        type=_parse_nonnegative_int,
        default=None,
        help="Compare two history snapshots by newest-first index, e.g. --history 0 1.",
    )
    swagger_diff_parser.add_argument(
        "--endpoint", metavar="NAME", default=None, help="Filter by root endpoint."
    )
    swagger_diff_parser.add_argument(
        "--method",
        choices=["get", "post", "put", "patch", "delete", "all"],
        default="all",
        help="HTTP method to inspect. Default: all.",
    )
    swagger_diff_parser.add_argument(
        "--include-nested",
        action="store_true",
        help="Include nested/action paths under the endpoint.",
    )
    swagger_diff_mode = swagger_diff_parser.add_mutually_exclusive_group()
    swagger_diff_mode.add_argument(
        "--fields-only", action="store_true", help="Only show field drift."
    )
    swagger_diff_mode.add_argument(
        "--operations-only", action="store_true", help="Only show operation drift."
    )
    swagger_diff_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

    swagger_coverage_parser = swagger_actions.add_parser(
        "coverage",
        help="Compare Swagger GET collections with fetch config",
    )
    swagger_coverage_parser.add_argument(
        "--fetch-config",
        metavar="PATH",
        default=str(DEFAULT_CONFIG_PATH),
        help="Fetcher config path.",
    )
    swagger_coverage_parser.add_argument(
        "--json", action="store_true", help="Emit one JSON object."
    )

    doctor_parser = subparsers.add_parser("doctor", help="Check local Centric API setup")
    doctor_parser.add_argument(
        "--fetch-config",
        metavar="PATH",
        default=str(DEFAULT_CONFIG_PATH),
        help="Fetcher config path.",
    )
    doctor_parser.add_argument(
        "--download-config", metavar="PATH", default=None, help="Download config path."
    )
    doctor_parser.add_argument(
        "--bundle-config", metavar="PATH", default=None, help="Bundle config path."
    )
    doctor_parser.add_argument(
        "--schema", metavar="PATH", default=None, help="Endpoint schema config path."
    )
    doctor_parser.add_argument("--db", metavar="PATH", default=None, help="SQLite database path.")
    doctor_parser.add_argument(
        "--env-file", metavar="PATH", default=None, help="Credential env file."
    )
    doctor_parser.add_argument("--json", action="store_true", help="Emit JSON Lines checks.")

    rebuild_parser = subparsers.add_parser("rebuild-db", help="Rebuild SQLite from raw evidence")
    rebuild_parser.add_argument("--db", metavar="PATH", default=None, help="SQLite database path.")
    rebuild_parser.add_argument(
        "--raw-dir", metavar="PATH", default=None, help="Raw evidence directory."
    )
    rebuild_parser.add_argument(
        "--schema", metavar="PATH", default=None, help="Endpoint schema config path."
    )
    rebuild_parser.add_argument("--yes", action="store_true", help="Confirm destructive rebuild.")
    rebuild_parser.add_argument(
        "--skip-changelog",
        action="store_true",
        help="Skip full changelog rebuild after raw ingest.",
    )
    rebuild_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")
    return parser


def _add_load_config_override(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--load-config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Load config path.",
    )


def _add_model_config_overrides(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--models-dir",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Private models directory.",
    )
    parser.add_argument(
        "--units-config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Units config path.",
    )


def _add_validate_config_overrides(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--validators-dir",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Private validators directory.",
    )
    parser.add_argument(
        "--units-config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Units config path.",
    )


def _add_snapshot_dir_override(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--snapshots-dir",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Private snapshots directory.",
    )


def _add_snapshot_config_overrides(parser: argparse.ArgumentParser) -> None:
    _add_snapshot_dir_override(parser)
    parser.add_argument(
        "--units-config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Units config path.",
    )


def _add_units_config_override(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--units-config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Units config path.",
    )


def _parse_days_back(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--days must be an integer.") from exc
    if parsed < MIN_DAYS_BACK or parsed > MAX_DAYS_BACK:
        raise argparse.ArgumentTypeError(
            f"--days must be between {MIN_DAYS_BACK} and {MAX_DAYS_BACK}."
        )
    return parsed


def _parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer.") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer.")
    return parsed


def _parse_nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer.") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be zero or a positive integer.")
    return parsed


def _parse_months_back(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--months must be an integer.") from exc
    if parsed < MIN_MONTHS_BACK or parsed > MAX_MONTHS_BACK:
        raise argparse.ArgumentTypeError(
            f"--months must be between {MIN_MONTHS_BACK} and {MAX_MONTHS_BACK}."
        )
    return parsed
