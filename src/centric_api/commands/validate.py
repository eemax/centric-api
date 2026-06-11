from __future__ import annotations

import argparse
import json

from ..config import ConfigError
from ..defaults import db_path as resolve_db_path
from ..rendering.common import print_rows
from ..rendering.validate import (
    print_human_validation_summary,
    print_human_validator_list,
    print_human_validator_show,
    validation_summary_record,
    validator_record,
)
from ..validation.registry import discover_validators, select_validator
from ..validation.runner import run_validator


def run_validate_command(args: argparse.Namespace) -> int:
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
    summaries = [
        run_validator(
            resolve_db_path(args.db),
            select_validator(validators, name),
            output_root=args.output_dir,
            units_config=args.units_config,
            mode=mode,
            input_file=args.input_file,
        )
        for name in names
    ]
    if args.json:
        for summary in summaries:
            print(json.dumps(validation_summary_record(summary), default=str))
    else:
        for index, summary in enumerate(summaries):
            if index:
                print()
            print_human_validation_summary(summary)
    return 0
