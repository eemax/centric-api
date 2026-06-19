from __future__ import annotations

import argparse

from ..defaults import DEFAULT_CONFIG_PATH
from .common import _add_units_config_override, _parse_nonnegative_int


def add_map_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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

def add_units_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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

def add_cron_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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

def add_status_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    status_parser = subparsers.add_parser("status", help="Show local Centric API status")
    status_parser.add_argument("--db", metavar="PATH", default=None, help="SQLite database path.")
    status_parser.add_argument("--json", action="store_true", help="Emit one JSON object.")

def add_swagger_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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

def add_doctor_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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

def add_rebuild_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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

