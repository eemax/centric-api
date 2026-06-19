from __future__ import annotations

import argparse

from ..defaults import (
    MAX_DAYS_BACK,
    MAX_MONTHS_BACK,
    MIN_DAYS_BACK,
    MIN_MONTHS_BACK,
)


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
