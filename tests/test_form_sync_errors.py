"""Tests for the form_sync error hierarchy.

The pipeline relies on TransientError being distinguishable from the other
FormSyncError types (it's the only one that leaves an email unread), so the
class relationships matter.
"""

from fit_file_faker.form_sync.errors import (
    ExpiredLinkError,
    FormSyncError,
    GarminAuthError,
    TransientError,
)


def test_all_subclass_form_sync_error():
    for cls in (TransientError, ExpiredLinkError, GarminAuthError):
        assert issubclass(cls, FormSyncError)


def test_form_sync_error_is_runtime_error():
    assert issubclass(FormSyncError, RuntimeError)


def test_permanent_errors_are_not_transient():
    """Only TransientError should leave an email unread for retry."""
    assert not issubclass(ExpiredLinkError, TransientError)
    assert not issubclass(GarminAuthError, TransientError)


def test_downloader_reexports_expired_link_error():
    """Existing code references downloader.ExpiredLinkError — keep it working."""
    from fit_file_faker.form_sync import downloader

    assert downloader.ExpiredLinkError is ExpiredLinkError
