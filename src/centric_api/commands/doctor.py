from __future__ import annotations

import argparse
import json

from .health import _doctor_checks, _print_human_doctor


def run_doctor(args: argparse.Namespace) -> int:
    checks = _doctor_checks(args)
    if args.json:
        for check in checks:
            print(json.dumps(check, default=str))
    else:
        _print_human_doctor(checks)
    return 1 if any(check["status"] == "FAIL" for check in checks) else 0


__all__ = ["run_doctor"]
