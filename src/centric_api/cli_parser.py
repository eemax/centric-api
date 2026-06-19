from __future__ import annotations

import sys

from ._cli_parser import build_parser

__all__ = ["build_parser", "normalize_argv"]


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
