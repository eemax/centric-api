from __future__ import annotations

import os
from typing import TextIO

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
MAGENTA = "\033[35m"
YELLOW = "\033[33m"


def should_color(stream: TextIO) -> bool:
    force_color = os.environ.get("FORCE_COLOR")
    if force_color and force_color.lower() not in {"0", "false", "no"}:
        return True
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return stream.isatty()


def top_level_help(*, color: bool = False) -> str:
    def paint(text: str, *codes: str) -> str:
        if not color:
            return text
        return f"{''.join(codes)}{text}{RESET}"

    def command(name: str, description: str) -> str:
        return f"  {paint(f'{name:<11}', GREEN)} {description}"

    def example(*parts: tuple[str, str]) -> str:
        colors = {
            "binary": MAGENTA,
            "command": GREEN,
            "flag": CYAN,
            "value": DIM,
            "path": YELLOW,
        }
        rendered = [paint(text, colors[kind]) for kind, text in parts]
        return f"  {' '.join(rendered)}"

    def step(number: int, name: str, description: str) -> str:
        marker = paint(f"{number}.", DIM)
        return f"  {marker} {paint(f'{name:<9}', GREEN)} {description}"

    return "\n".join(
        [
            paint("CENTRIC API", BOLD, CYAN),
            "",
            paint("Usage:", BOLD),
            f"  {paint('centric-api', MAGENTA)} <command> [options]",
            "",
            paint("Recommended path:", BOLD),
            step(1, "doctor", "Verify config, credentials, cache, and locks"),
            step(2, "fetch", "Pull records into the local SQLite cache"),
            step(3, "status", "Confirm what is cached locally"),
            step(4, "view", "Export clean Excel/CSV views"),
            "",
            paint("Core workflows:", BOLD),
            command("fetch", "Pull Centric records into SQLite"),
            command("view", "Export cache data to Excel/CSV"),
            command("validate", "Run private cache validation reports"),
            command("load", "Validate Excel rows and push data to Centric"),
            command("map", "Generate endpoint relationship maps"),
            command("changelog", "Review changed records and actor activity"),
            command("download", "Download current document revisions"),
            command("bundle", "Package downloaded files"),
            "",
            paint("System & advanced:", BOLD),
            command("doctor", "Check local setup"),
            command("status", "Show cache and runtime state"),
            command("rebuild-db", "Rebuild SQLite from raw evidence"),
            command("swagger", "Inspect and refresh local API schema"),
            command("cron", "Run scheduled delta fetches"),
            command("units", "Inspect and convert configured units"),
            command("model", "Run private calculated data models"),
            "",
            paint("Good first commands:", BOLD),
            example(("binary", "centric-api"), ("command", "doctor")),
            example(
                ("binary", "centric-api"),
                ("command", "fetch"),
                ("flag", "--endpoint"),
                ("value", "styles"),
            ),
            example(("binary", "centric-api"), ("command", "status")),
            example(
                ("binary", "centric-api"),
                ("command", "view"),
                ("command", "export"),
                ("value", "style-colorways-demo"),
            ),
            example(
                ("binary", "centric-api"),
                ("command", "validate"),
                ("command", "list"),
            ),
            example(
                ("binary", "centric-api"),
                ("command", "load"),
                ("command", "check"),
                ("value", "material-create"),
                ("path", "materials.xlsx"),
            ),
            "",
            paint("Use command help for details:", BOLD),
            example(("binary", "centric-api"), ("command", "load"), ("flag", "--help")),
            example(("binary", "centric-api"), ("command", "view"), ("flag", "--help")),
            example(("binary", "centric-api"), ("command", "fetch"), ("flag", "--help")),
            "",
        ]
    )
