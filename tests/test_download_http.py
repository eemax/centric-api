from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from centric_api.download_http import download_revision_file
from tests.helpers_download import _Auth


def test_download_revision_file_uses_content_disposition_filename(tmp_path: Path) -> None:
    auth = _Auth(
        httpx.Response(
            200,
            headers={
                "content-disposition": 'inline;filename="real-name.pdf"',
                "content-type": "application/pdf",
            },
            content=b"hello",
        )
    )

    result = download_revision_file(
        auth,
        revision_id="R1",
        target_path=tmp_path / "fallback.pdf",
        fallback_filename="fallback.pdf",
    )

    assert result.path == tmp_path / "real-name.pdf"
    assert result.path.read_bytes() == b"hello"
    assert result.bytes_written == 5
    assert result.content_type == "application/pdf"

def test_download_revision_file_retries_retryable_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[int] = []
    monkeypatch.setattr("centric_api.download_http.time.sleep", sleeps.append)
    auth = _Auth(
        [
            httpx.Response(503, content=b"reload"),
            httpx.Response(200, content=b"ok"),
        ]
    )

    result = download_revision_file(
        auth,
        revision_id="R1",
        target_path=tmp_path / "fallback.pdf",
        fallback_filename="fallback.pdf",
    )

    assert result.path.read_bytes() == b"ok"
    assert sleeps == [15]


def test_download_revision_file_rejects_content_length_mismatch(tmp_path: Path) -> None:
    auth = _Auth(
        httpx.Response(
            200,
            headers={"content-length": "5"},
            content=b"ok",
        )
    )
    target_path = tmp_path / "fallback.pdf"

    with pytest.raises(RuntimeError, match="content length mismatch"):
        download_revision_file(
            auth,
            revision_id="R1",
            target_path=target_path,
            fallback_filename="fallback.pdf",
        )

    assert not target_path.exists()
    assert not (tmp_path / ".fallback.pdf.tmp").exists()
