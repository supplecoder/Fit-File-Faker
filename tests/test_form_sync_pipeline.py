"""Tests for fit_file_faker.form_sync.pipeline.

The most important behavior here is the read/unread policy: an email is marked
read on success and on permanent failures, but left UNREAD on transient
failures so it retries. Also covers env handling, firmware lookup, remediation
text, and the failure summary.
"""

from unittest.mock import MagicMock, patch

import pytest

from fit_file_faker.form_sync import pipeline
from fit_file_faker.form_sync.errors import (
    ExpiredLinkError,
    GarminAuthError,
    TransientError,
)


class TestRequireEnv:
    def test_returns_value(self, monkeypatch):
        monkeypatch.setenv("FOO", "bar")
        assert pipeline._require_env("FOO") == "bar"

    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("FOO", raising=False)
        with pytest.raises(RuntimeError):
            pipeline._require_env("FOO")

    def test_raises_when_blank(self, monkeypatch):
        monkeypatch.setenv("FOO", "   ")
        with pytest.raises(RuntimeError):
            pipeline._require_env("FOO")


class TestLookupSoftwareVersion:
    def test_known_device_returns_firmware(self):
        # Forerunner 965 = 4315, present in the supplemental registry
        version = pipeline._lookup_software_version(4315)
        assert isinstance(version, int)
        assert version > 0

    def test_unknown_device_raises(self):
        with pytest.raises(RuntimeError):
            pipeline._lookup_software_version(99999999)


class TestRemediation:
    def test_expired_link_mentions_reexport(self):
        assert "Re-export" in pipeline._remediation(ExpiredLinkError("x"))

    def test_auth_mentions_reseed(self):
        msg = pipeline._remediation(GarminAuthError("x"))
        assert "seed_garmin_tokens" in msg

    def test_transient_says_no_action(self):
        assert "No action needed" in pipeline._remediation(TransientError("x"))

    def test_unknown_falls_back(self):
        assert "Unexpected error" in pipeline._remediation(ValueError("x"))


class TestWriteFailureSummary:
    def test_writes_to_github_step_summary(self, tmp_path, monkeypatch):
        summary_file = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        pipeline._write_failure_summary([("263", ExpiredLinkError("x"))])
        content = summary_file.read_text(encoding="utf-8")
        assert "Email 263" in content
        assert "Re-export" in content

    def test_falls_back_to_log_without_env(self, monkeypatch, caplog):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        with caplog.at_level("ERROR"):
            pipeline._write_failure_summary([("1", TransientError("x"))])
        assert any("Failure summary" in r.message for r in caplog.records)


class TestProcessEmailReadPolicy:
    """The crux: which failures mark the email read vs leave it unread."""

    def _run(self, sync_side_effect):
        """Call _process_email with _sync_email mocked, return the mock conn."""
        conn = MagicMock()
        with patch.object(pipeline, "_sync_email", side_effect=sync_side_effect), \
             patch.object(pipeline.gmail, "mark_as_read") as mark_read:
            kwargs = dict(
                conn=conn, msg_id="1", garmin_email="e", garmin_password="p",
                garmin_tokens="t", device_id=4315, serial_number=1,
                software_version=1, gh_pat="g", gh_repo="r",
            )
            try:
                result = pipeline._process_email(**kwargs)
            except Exception as e:
                return mark_read, e
            return mark_read, result

    def test_success_marks_read(self):
        mark_read, result = self._run(lambda **k: "new-token")
        assert result == "new-token"
        mark_read.assert_called_once()

    def test_transient_error_leaves_unread(self):
        def boom(**k):
            raise TransientError("network")
        mark_read, exc = self._run(boom)
        assert isinstance(exc, TransientError)
        mark_read.assert_not_called()

    def test_expired_link_marks_read(self):
        def boom(**k):
            raise ExpiredLinkError("dead")
        mark_read, exc = self._run(boom)
        assert isinstance(exc, ExpiredLinkError)
        mark_read.assert_called_once()

    def test_auth_error_marks_read(self):
        def boom(**k):
            raise GarminAuthError("rejected")
        mark_read, exc = self._run(boom)
        assert isinstance(exc, GarminAuthError)
        mark_read.assert_called_once()

    def test_unknown_error_marks_read(self):
        def boom(**k):
            raise ValueError("weird")
        mark_read, exc = self._run(boom)
        assert isinstance(exc, ValueError)
        mark_read.assert_called_once()
