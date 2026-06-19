from __future__ import annotations

import argparse

from .common import (
    _add_load_config_override,
    _add_model_config_overrides,
    _add_snapshot_config_overrides,
    _add_snapshot_dir_override,
    _add_validate_config_overrides,
    _parse_positive_int,
)


def add_view_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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


def add_load_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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


def add_model_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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


def add_validate_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
        choices=("run", "day", "week", "month"),
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


def add_snapshot_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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

    snapshot_diff_parser = snapshot_actions.add_parser(
        "diff",
        help="Compare candidate snapshot artifacts against baseline",
    )
    snapshot_diff_parser.add_argument("name", metavar="NAME", help="Snapshot name.")
    _add_snapshot_dir_override(snapshot_diff_parser)
    snapshot_diff_parser.add_argument(
        "--output-dir",
        metavar="PATH",
        default=None,
        help="Snapshot workspace root directory.",
    )
    snapshot_diff_parser.add_argument(
        "--review-file",
        metavar="PATH",
        default=None,
        help="Write JSON review actions for selective promotion.",
    )
    snapshot_diff_parser.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="SQLite database path for review display hydration.",
    )
    snapshot_diff_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

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
        "--review-file",
        metavar="PATH",
        default=None,
        help="Apply approved JSON review actions instead of promoting the whole candidate.",
    )
    snapshot_promote_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm full candidate-to-baseline promotion when no review file is used.",
    )
    snapshot_promote_parser.add_argument(
        "--json", action="store_true", help="Emit one JSON object."
    )
