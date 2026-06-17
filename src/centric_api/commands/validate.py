from __future__ import annotations

import argparse
import json
import sys
import time

from ..config import ConfigError
from ..defaults import db_path as resolve_db_path
from ..rendering.common import print_rows
from ..rendering.logs import format_duration
from ..rendering.validate import (
    print_human_validation_summary,
    print_human_validator_list,
    print_human_validator_show,
    validation_summary_record,
    validator_record,
)
from ..validation.history import build_validation_history
from ..validation.registry import discover_validators, select_validator
from ..validation.runner import run_validator


def run_validate_command(args: argparse.Namespace) -> int:
    if args.action == "history":
        summary = build_validation_history(
            runs_dir=args.runs_dir,
            output_dir=args.output_dir,
            group=args.group,
            validators=tuple(args.validator),
        )
        payload = {
            "group": summary.group,
            "runs_dir": str(summary.runs_dir),
            "output_dir": str(summary.output_dir),
            "json_path": str(summary.json_path),
            "html_path": str(summary.html_path),
            "raw_metric_count": summary.raw_metric_count,
            "point_count": summary.point_count,
            "run_count": summary.run_count,
            "validators": list(summary.validators),
            "metrics": list(summary.metrics),
        }
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            print("Validation History")
            print()
            print(f"Group:     {summary.group}")
            print(f"Runs:      {summary.run_count}")
            print(f"Metrics:   {summary.raw_metric_count} raw, {summary.point_count} grouped")
            print(f"HTML:      {summary.html_path}")
            print(f"JSON:      {summary.json_path}")
        return 0

    validators = discover_validators(args.validators_dir)
    if args.action == "list":
        rows = [validator_record(validator) for validator in validators]
        if args.json:
            return print_rows(rows, True, empty_message="No validators found.")
        print_human_validator_list(validators)
        return 0

    if args.action == "show":
        validator = select_validator(validators, args.name)
        payload = validator_record(validator)
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            print_human_validator_show(validator)
        return 0

    if args.name == "all":
        if not validators:
            raise ConfigError("No validators found.")
        names = [validator.definition.name for validator in validators]
    else:
        names = [args.name]
    mode = args.mode or ("excel" if args.input_file else "cache")
    if args.input_file and args.mode == "cache":
        raise ConfigError("--input-file requires --mode excel or no --mode.")
    db_path = resolve_db_path(args.db)
    started = time.time()
    if not args.json:
        _print_validate_progress(
            f"Validation run: validators={len(names)} mode={mode} db={db_path}"
        )
    summaries = []
    for index, name in enumerate(names, start=1):
        validator = select_validator(validators, name)
        validator_started = time.time()
        if not args.json:
            _print_validate_progress(f"[{validator.definition.name}] START  {index}/{len(names)}")
        summary = run_validator(
            db_path,
            validator,
            output_root=args.output_dir,
            units_config=args.units_config,
            mode=mode,
            input_file=args.input_file,
        )
        summaries.append(summary)
        if not args.json:
            _print_validate_progress(
                f"[{summary.validator_name}] DONE   status={summary.status} "
                f"findings={summary.finding_count} "
                f"elapsed={format_duration(time.time() - validator_started)}"
            )
    if not args.json:
        total_findings = sum(summary.finding_count for summary in summaries)
        _print_validate_progress(
            f"validation=done validators={len(summaries)} "
            f"findings={total_findings} elapsed={format_duration(time.time() - started)}"
        )
        _print_validate_progress("")
    if args.json:
        for summary in summaries:
            print(json.dumps(validation_summary_record(summary), default=str))
    else:
        for index, summary in enumerate(summaries):
            if index:
                print()
            print_human_validation_summary(summary)
    return 0


def _print_validate_progress(message: str) -> None:
    print(message, file=sys.stderr)
