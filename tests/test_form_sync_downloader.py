"""Tests for fit_file_faker.form_sync.downloader.

Covers URL redaction, S3 error parsing, download-failure classification
(expired vs transient vs permanent), and ZIP extraction.
"""

import zipfile
from unittest.mock import MagicMock, patch

import pytest
import requests

from fit_file_faker.form_sync import downloader
from fit_file_faker.form_sync.errors import ExpiredLinkError, TransientError

# A presigned-style URL whose query string carries a (fake) security token.
SIGNED_URL = (
    "https://formdata-us-east-1.s3.us-east-1.amazonaws.com/archives/abc/file.zip"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Security-Token=SECRET_TOKEN_VALUE"
    "&X-Amz-Signature=deadbeef"
)


def _response(status_code, text="", content=b"data"):
    """Build a fake requests.Response-like object."""
    resp = MagicMock()
    resp.ok = status_code < 400
    resp.status_code = status_code
    resp.text = text
    resp.content = content
    return resp


class TestRedactUrl:
    def test_strips_query_string(self):
        redacted = downloader._redact_url(SIGNED_URL)
        assert redacted.endswith("/archives/abc/file.zip")
        assert "?" not in redacted

    def test_never_exposes_token(self):
        assert "SECRET_TOKEN_VALUE" not in downloader._redact_url(SIGNED_URL)


class TestParseS3Error:
    def test_extracts_code_and_message(self):
        body = "<Error><Code>ExpiredToken</Code><Message>The provided token has expired.</Message></Error>"
        assert downloader._parse_s3_error(body) == "ExpiredToken: The provided token has expired."

    def test_never_exposes_token_in_body(self):
        body = (
            "<Error><Code>ExpiredToken</Code><Message>expired</Message>"
            "<Token-0>SECRET_TOKEN_VALUE</Token-0></Error>"
        )
        assert "SECRET_TOKEN_VALUE" not in downloader._parse_s3_error(body)

    def test_handles_missing_fields(self):
        assert downloader._parse_s3_error("") == "Unknown: (no message)"


class TestDownloadZipClassification:
    """download_zip must classify failures into the right exception type."""

    def test_expired_token_raises_expired_link_error(self, tmp_path):
        body = "<Error><Code>ExpiredToken</Code><Message>expired</Message></Error>"
        with patch("requests.get", return_value=_response(400, body)):
            with pytest.raises(ExpiredLinkError):
                downloader.download_zip(SIGNED_URL, tmp_path)

    def test_403_raises_expired_link_error(self, tmp_path):
        body = "<Error><Code>AccessDenied</Code><Message>denied</Message></Error>"
        with patch("requests.get", return_value=_response(403, body)):
            with pytest.raises(ExpiredLinkError):
                downloader.download_zip(SIGNED_URL, tmp_path)

    def test_5xx_raises_transient_error(self, tmp_path):
        body = "<Error><Code>SlowDown</Code><Message>slow</Message></Error>"
        with patch("requests.get", return_value=_response(503, body)):
            with pytest.raises(TransientError):
                downloader.download_zip(SIGNED_URL, tmp_path)

    def test_network_error_raises_transient_error(self, tmp_path):
        with patch("requests.get", side_effect=requests.exceptions.ConnectionError("boom")):
            with pytest.raises(TransientError):
                downloader.download_zip(SIGNED_URL, tmp_path)

    def test_timeout_raises_transient_error(self, tmp_path):
        with patch("requests.get", side_effect=requests.exceptions.Timeout("slow")):
            with pytest.raises(TransientError):
                downloader.download_zip(SIGNED_URL, tmp_path)

    def test_other_4xx_raises_runtime_error(self, tmp_path):
        body = "<Error><Code>NoSuchKey</Code><Message>missing</Message></Error>"
        with patch("requests.get", return_value=_response(404, body)):
            with pytest.raises(RuntimeError) as exc:
                downloader.download_zip(SIGNED_URL, tmp_path)
            # Must be a plain RuntimeError, not a transient/expired subclass
            assert not isinstance(exc.value, (TransientError, ExpiredLinkError))

    def test_error_message_redacts_url(self, tmp_path):
        body = "<Error><Code>NoSuchKey</Code><Message>missing</Message></Error>"
        with patch("requests.get", return_value=_response(404, body)):
            with pytest.raises(RuntimeError) as exc:
                downloader.download_zip(SIGNED_URL, tmp_path)
            assert "SECRET_TOKEN_VALUE" not in str(exc.value)

    def test_success_writes_file(self, tmp_path):
        with patch("requests.get", return_value=_response(200, content=b"zipbytes")):
            path = downloader.download_zip(SIGNED_URL, tmp_path)
        assert path.exists()
        assert path.read_bytes() == b"zipbytes"


class TestExtractFitFiles:
    def _make_zip(self, path, names):
        with zipfile.ZipFile(path, "w") as zf:
            for name in names:
                zf.writestr(name, b"fitdata")

    def test_extracts_only_fit_files(self, tmp_path):
        zip_path = tmp_path / "export.zip"
        self._make_zip(zip_path, ["swim.fit", "readme.txt", "other.FIT"])
        out = tmp_path / "out"
        out.mkdir()
        result = downloader.extract_fit_files(zip_path, out)
        names = sorted(p.name for p in result)
        assert names == ["other.FIT", "swim.fit"]

    def test_empty_when_no_fit_files(self, tmp_path):
        zip_path = tmp_path / "export.zip"
        self._make_zip(zip_path, ["readme.txt", "data.csv"])
        out = tmp_path / "out"
        out.mkdir()
        assert downloader.extract_fit_files(zip_path, out) == []
