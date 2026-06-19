from __future__ import annotations

import argparse

from .data import (
    add_bundle_parser,
    add_changelog_parser,
    add_download_parser,
    add_fetch_parser,
    add_ingest_parser,
    add_raw_parser,
)
from .system import (
    add_cron_parser,
    add_doctor_parser,
    add_map_parser,
    add_rebuild_parser,
    add_status_parser,
    add_swagger_parser,
    add_units_parser,
)
from .workflow import (
    add_load_parser,
    add_model_parser,
    add_snapshot_parser,
    add_validate_parser,
    add_view_parser,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="centric-api")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_fetch_parser(subparsers)
    add_ingest_parser(subparsers)
    add_raw_parser(subparsers)
    add_changelog_parser(subparsers)
    add_download_parser(subparsers)
    add_bundle_parser(subparsers)
    add_view_parser(subparsers)
    add_load_parser(subparsers)
    add_model_parser(subparsers)
    add_validate_parser(subparsers)
    add_snapshot_parser(subparsers)
    add_map_parser(subparsers)
    add_units_parser(subparsers)
    add_cron_parser(subparsers)
    add_status_parser(subparsers)
    add_swagger_parser(subparsers)
    add_doctor_parser(subparsers)
    add_rebuild_parser(subparsers)
    return parser
