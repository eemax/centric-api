from __future__ import annotations

import sys

from .auth import AuthError
from .cli_parser import build_parser, normalize_argv
from .commands.bundle import run_bundle
from .commands.changelog import run_changelog
from .commands.cron import run_cron
from .commands.doctor import run_doctor
from .commands.download import run_download
from .commands.fetch import run_fetch
from .commands.ingest import run_ingest_command
from .commands.load import run_load_command
from .commands.map import run_map_command
from .commands.model import run_model_command
from .commands.rebuild_db import run_rebuild_db
from .commands.snapshot import run_snapshot_command
from .commands.status import run_status
from .commands.swagger import run_swagger
from .commands.units import run_units
from .commands.validate import run_validate_command
from .commands.view import run_view
from .config import ConfigError
from .fetcher import FetchError
from .rendering.help import should_color, top_level_help


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    if raw_argv in (["--help"], ["-h"]):
        print(top_level_help(color=should_color(sys.stdout)), end="")
        return 0
    parser = build_parser()
    args = parser.parse_args(normalize_argv(raw_argv))
    try:
        if args.command == "fetch":
            return run_fetch(args)
        if args.command == "ingest":
            return run_ingest_command(args)
        if args.command == "changelog":
            return run_changelog(args)
        if args.command == "cron":
            return run_cron(args)
        if args.command == "download":
            return run_download(args)
        if args.command == "bundle":
            return run_bundle(args)
        if args.command == "view":
            return run_view(args)
        if args.command == "load":
            return run_load_command(args)
        if args.command == "map":
            return run_map_command(args)
        if args.command == "model":
            return run_model_command(args)
        if args.command == "validate":
            return run_validate_command(args)
        if args.command == "snapshot":
            return run_snapshot_command(args)
        if args.command == "units":
            return run_units(args)
        if args.command == "status":
            return run_status(args)
        if args.command == "swagger":
            return run_swagger(args)
        if args.command == "doctor":
            return run_doctor(args)
        if args.command == "rebuild-db":
            return run_rebuild_db(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except (AuthError, ConfigError, FetchError, FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
