from __future__ import annotations

import argparse
import json

from ..defaults import db_path as resolve_db_path
from ..modeling.registry import discover_models, select_model
from ..modeling.runner import check_model, run_model
from ..rendering.common import print_rows
from ..rendering.model import (
    model_record,
    print_human_model_list,
    print_human_model_show,
    print_human_model_summary,
    summary_record,
)


def run_model_command(args: argparse.Namespace) -> int:
    models = discover_models(args.models_dir)
    if args.action == "list":
        rows = [model_record(model) for model in models]
        if args.json:
            return print_rows(rows, True, empty_message="No models found.")
        print_human_model_list(models)
        return 0

    model = select_model(models, args.name)
    if args.action == "show":
        payload = model_record(model)
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            print_human_model_show(model)
        return 0

    if args.action == "check":
        summary = check_model(
            resolve_db_path(args.db),
            model,
            units_config=args.units_config,
        )
    else:
        summary = run_model(
            resolve_db_path(args.db),
            model,
            units_config=args.units_config,
        )
    if args.json:
        print(json.dumps(summary_record(summary), default=str))
    else:
        print_human_model_summary(summary)
    return 0
