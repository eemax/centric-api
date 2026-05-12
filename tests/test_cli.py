from __future__ import annotations

import pytest

from centric_api.cli import _parse_jsonl, main


def test_cli_help_commands(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "fetch" in output
    assert "changelog" in output
    assert "cron" in output


def test_changelog_summary_empty_db(tmp_path, capsys) -> None:
    exit_code = main(["changelog", "--db", str(tmp_path / "centric.db")])

    assert exit_code == 0
    assert "No changelog events found." in capsys.readouterr().out


def test_fetch_and_cron_help_are_lean(capsys) -> None:
    with pytest.raises(SystemExit) as fetch_exc:
        main(["fetch", "--help"])
    assert fetch_exc.value.code == 0
    fetch_help = capsys.readouterr().out
    assert "--fetch-config" in fetch_help
    assert "--config" not in fetch_help
    assert "--timeout" not in fetch_help

    with pytest.raises(SystemExit) as cron_exc:
        main(["cron", "--help"])
    assert cron_exc.value.code == 0
    cron_help = capsys.readouterr().out
    assert "--fetch-config" in cron_help
    assert "--config" not in cron_help
    assert "--timeout" not in cron_help
    assert "[schedule]" in cron_help


def test_parse_jsonl_preserves_non_json_lines() -> None:
    assert _parse_jsonl('{"status":"ok"}\nnot-json\n') == [
        {"status": "ok"},
        {"record_type": "fetch_stdout", "line": "not-json"},
    ]
