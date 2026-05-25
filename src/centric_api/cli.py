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
from .commands.model import run_model_command
from .commands.rebuild_db import run_rebuild_db
from .commands.status import run_status
from .commands.units import run_units
from .commands.view import run_view
from .config import ConfigError
from .fetcher import FetchError


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(argv))
    try:
        if args.command == "fetch":
            return run_fetch(args)
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
        if args.command == "model":
            return run_model_command(args)
        if args.command == "units":
            return run_units(args)
        if args.command == "status":
            return run_status(args)
        if args.command == "doctor":
            return run_doctor(args)
        if args.command == "rebuild-db":
            return run_rebuild_db(args)
    except (AuthError, ConfigError, FetchError, FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
