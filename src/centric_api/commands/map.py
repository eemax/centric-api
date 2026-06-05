from __future__ import annotations

import argparse
import json

from ..defaults import db_path as resolve_db_path
from ..endpoint_map import build_endpoint_map
from ..rendering.map import endpoint_map_result_record, print_human_endpoint_map_result


def run_map_command(args: argparse.Namespace) -> int:
    result = build_endpoint_map(
        resolve_db_path(args.db),
        output_root=args.output_dir,
    )
    payload = endpoint_map_result_record(result)
    if args.json:
        print(json.dumps(payload, default=str))
    else:
        print_human_endpoint_map_result(result)
    return 0
