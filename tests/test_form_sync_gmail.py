"""Tests for fit_file_faker.form_sync.gmail.

Covers the search criteria, the BODY.PEEK fetch (so reading doesn't mark
the email seen), S3 URL extraction from HTML bodies, and mark_as_read.
"""

import email
from email.message import EmailMessage
from unittest.mock import MagicMock

from fit_file_faker.form_sync import gmail

S3_URL = (
    "https://formdata-us-east-1.s3.us-east-1.amazonaws.com/archives/abc/file.zip"
    "?X-Amz-Signature=deadbeef"
)


def _html_email(html):
    msg = EmailMessage()
    msg["From"] = gmail.FORM_SENDER
    msg["Subject"] = gmail.FORM_SUBJECT
    msg.set_content("plain text fallback")
    msg.add_alternative(html, subtype="html")
    return email.message_from_bytes(msg.as_bytes())


class TestSearchFormEmails:
    def test_builds_criteria_and_parses_ids(self):
        conn = MagicMock()
        conn.search.return_value = ("OK", [b"1 2 3"])
        ids = gmail.search_form_emails(conn)
        assert ids == ["1", "2", "3"]
        criteria = conn.search.call_args[0][1]
        assert "UNSEEN" in criteria
        assert gmail.FORM_SENDER in criteria
        assert gmail.FORM_SUBJECT in criteria

    def test_empty_results(self):
        conn = MagicMock()
        conn.search.return_value = ("OK", [b""])
        assert gmail.search_form_emails(conn) == []


class TestFetchEmail:
    def test_uses_body_peek_to_avoid_marking_seen(self):
        """A plain RFC822 fetch would set \\Seen; we must use BODY.PEEK[]."""
        conn = MagicMock()
        raw = _html_email('<a href="x">Download</a>').as_bytes()
        conn.fetch.return_value = ("OK", [(b"1", raw)])
        gmail.fetch_email(conn, "1")
        fetch_arg = conn.fetch.call_args[0][1]
        assert "BODY.PEEK" in fetch_arg
        assert "RFC822" not in fetch_arg


class TestExtractS3Url:
    def test_extracts_download_link(self):
        msg = _html_email(f'<p>hi</p><a href="{S3_URL}">Download Data</a>')
        assert gmail.extract_s3_url(msg) == S3_URL

    def test_picks_first_download_link(self):
        html = (
            '<a href="https://first/Download.zip">Download</a>'
            '<a href="https://second/Download.zip">Download</a>'
        )
        msg = _html_email(html)
        assert gmail.extract_s3_url(msg) == "https://first/Download.zip"

    def test_decodes_html_entities_in_href(self):
        url = "https://x/file.zip?a=1&amp;b=2&amp;c=3"
        msg = _html_email(f'<a href="{url}">Download</a>')
        assert gmail.extract_s3_url(msg) == "https://x/file.zip?a=1&b=2&c=3"

    def test_returns_none_without_download_link(self):
        msg = _html_email('<a href="https://x">Unsubscribe</a>')
        assert gmail.extract_s3_url(msg) is None


class TestMarkAsRead:
    def test_sets_seen_flag(self):
        conn = MagicMock()
        gmail.mark_as_read(conn, "7")
        conn.store.assert_called_once_with("7", "+FLAGS", "\\Seen")
