from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..defaults import db_path as resolve_db_path
from ..rendering.common import print_rows
from ..rendering.view import (
    export_record,
    print_human_view_export,
    print_human_view_list,
    print_human_view_show,
    view_record,
)
from ..view_config import load_view_config, select_view
from ..view_export import export_view, infer_export_format


def run_view(args: argparse.Namespace) -> int:
    config = load_view_config(args.view_config)
    if args.action == "list":
        rows = [view_record(view) for view in config.views]
        if args.json:
            return print_rows(rows, True, empty_message="No views found.")
        print_human_view_list(config.views)
        return 0

    view = select_view(config, args.name)
    if args.action == "show":
        payload = view_record(view)
        if args.json:
            print(json.dumps(payload, default=str))
        else:
            print_human_view_show(view)
        return 0

    output_path = Path(args.output).expanduser() if args.output else None
    export_format = infer_export_format(output_path, args.format)
    result = export_view(
        resolve_db_path(args.db),
        config,
        view,
        export_format=export_format,
        output_path=output_path,
    )
    if args.json:
        print(json.dumps(export_record(result), default=str))
    else:
        print_human_view_export(result)
    return 0
