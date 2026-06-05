from __future__ import annotations

from typing import Any

from ..endpoint_map import EndpointMapResult
from .common import format_count


def endpoint_map_result_record(result: EndpointMapResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "artifact_dir": str(result.artifact_dir),
        "json_path": str(result.json_path),
        "markdown_path": str(result.markdown_path),
        "html_path": str(result.html_path),
        "endpoint_count": result.endpoint_count,
        "relationship_count": result.relationship_count,
    }


def print_human_endpoint_map_result(result: EndpointMapResult) -> None:
    print("Endpoint map complete")
    print()
    print(f"Run:           {result.run_id}")
    print(f"Endpoints:     {format_count(result.endpoint_count)}")
    print(f"Relationships: {format_count(result.relationship_count)}")
    print(f"Artifacts:     {result.artifact_dir}")
    print(f"JSON:          {result.json_path}")
    print(f"Markdown:      {result.markdown_path}")
    print(f"HTML:          {result.html_path}")
