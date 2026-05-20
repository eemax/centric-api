from __future__ import annotations

import argparse
import json

from ..defaults import db_path
from .health import _print_human_status, _status_payload


def run_status(args: argparse.Namespace) -> int:
    payload = _status_payload(db_path(args.db))
    if args.json:
        print(json.dumps(payload, default=str))
    else:
        _print_human_status(payload)
    return 0


__all__ = ["run_status"]
