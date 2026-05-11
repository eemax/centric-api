from __future__ import annotations

import pytest

from centric_api.cli import main


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
