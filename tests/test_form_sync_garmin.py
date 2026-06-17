"""Tests for fit_file_faker.form_sync.garmin.

Covers token-based vs fresh login, the 409 duplicate handling, transient
upload error classification, and auth-failure classification.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from garminconnect import GarminConnectConnectionError

from fit_file_faker.form_sync import garmin
from fit_file_faker.form_sync.errors import GarminAuthError, TransientError

LONG_TOKEN = "t" * 600  # > 512 chars, treated as token data by garminconnect
REFRESHED = "refreshed-token-string"


def _client():
    """A fake Garmin client whose .client.dumps() returns a known token."""
    client = MagicMock()
    client.client.dumps.return_value = REFRESHED
    return client


class TestUploadFit:
    def test_logs_in_with_stored_token(self, tmp_path):
        client = _client()
        with patch("fit_file_faker.form_sync.garmin.Garmin", return_value=client):
            result = garmin.upload_fit(Path("a.fit"), "e@x.com", "pw", LONG_TOKEN)
        client.login.assert_called_once()
        assert client.login.call_args.kwargs.get("tokenstore") == LONG_TOKEN
        assert result == REFRESHED

    def test_fresh_login_when_no_token(self, tmp_path):
        client = _client()
        with patch("fit_file_faker.form_sync.garmin.Garmin", return_value=client):
            garmin.upload_fit(Path("a.fit"), "e@x.com", "pw", "")
        client.login.assert_called_once()
        # No tokenstore kwarg on a fresh login
        assert "tokenstore" not in client.login.call_args.kwargs

    def test_uploads_activity_and_returns_refreshed_token(self):
        client = _client()
        with patch("fit_file_faker.form_sync.garmin.Garmin", return_value=client):
            result = garmin.upload_fit(Path("a.fit"), "e@x.com", "pw", LONG_TOKEN)
        client.upload_activity.assert_called_once()
        assert result == REFRESHED

    def test_409_is_treated_as_success(self):
        client = _client()
        client.upload_activity.side_effect = GarminConnectConnectionError("Error 409: dup")
        with patch("fit_file_faker.form_sync.garmin.Garmin", return_value=client):
            result = garmin.upload_fit(Path("a.fit"), "e@x.com", "pw", LONG_TOKEN)
        # No exception, still returns the refreshed token
        assert result == REFRESHED

    def test_non_409_upload_error_is_transient(self):
        client = _client()
        client.upload_activity.side_effect = GarminConnectConnectionError("Error 503: down")
        with patch("fit_file_faker.form_sync.garmin.Garmin", return_value=client):
            with pytest.raises(TransientError):
                garmin.upload_fit(Path("a.fit"), "e@x.com", "pw", LONG_TOKEN)

    def test_login_failure_is_auth_error(self):
        client = _client()
        client.login.side_effect = Exception("token rejected")
        with patch("fit_file_faker.form_sync.garmin.Garmin", return_value=client):
            with pytest.raises(GarminAuthError):
                garmin.upload_fit(Path("a.fit"), "e@x.com", "pw", "")
