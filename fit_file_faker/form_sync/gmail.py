"""Gmail IMAP client for fetching FORM export emails.

Connects to Gmail via IMAP using an App Password (no OAuth required).
Searches for unread FORM export emails, extracts the S3 download URL
from the HTML body, and marks emails as read after successful processing.

All search criteria are evaluated server-side by Gmail, so only matching
message IDs are returned — the full mailbox is never scanned locally.
"""

import imaplib
import email
import logging
from email.message import Message
from html.parser import HTMLParser
from typing import Optional

_logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
FORM_SENDER = "community@formswim.com"
FORM_SUBJECT = "Your FORM Data is ready!"


class _DownloadLinkExtractor(HTMLParser):
    """Extracts the href from the first anchor tag whose text contains 'Download'."""

    def __init__(self):
        super().__init__()
        self._current_href: Optional[str] = None
        self._in_anchor = False
        self.download_url: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "a":
            self._current_href = dict(attrs).get("href")
            self._in_anchor = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_anchor = False
            self._current_href = None

    def handle_data(self, data: str) -> None:
        if self._in_anchor and "Download" in data and self._current_href:
            if self.download_url is None:  # take the first match only
                self.download_url = self._current_href


def connect(gmail_address: str, app_password: str) -> imaplib.IMAP4_SSL:
    """Open an authenticated IMAP SSL connection to Gmail and select INBOX.

    Args:
        gmail_address: Full Gmail address (e.g. user@gmail.com).
        app_password: 16-character Google App Password.

    Returns:
        An open, authenticated IMAP4_SSL connection with INBOX selected.
    """
    _logger.info("Connecting to Gmail IMAP")
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(gmail_address, app_password)
    conn.select("INBOX")
    _logger.info("Gmail IMAP connection established")
    return conn


def search_form_emails(conn: imaplib.IMAP4_SSL) -> list[str]:
    """Search INBOX for unread FORM export emails.

    The search runs server-side on Gmail using three combined criteria:
    - UNSEEN: only emails not yet marked as read
    - FROM: sender must be community@formswim.com
    - SUBJECT: must match the FORM export subject exactly

    Using UNSEEN as the processed-state flag means we never re-process
    an email — once we mark it read at the end of the pipeline, it drops
    out of future search results permanently.

    Args:
        conn: An open, authenticated IMAP connection with INBOX selected.

    Returns:
        List of message ID strings (may be empty if no matching emails).
    """
    criteria = f'(UNSEEN FROM "{FORM_SENDER}" SUBJECT "{FORM_SUBJECT}")'
    _logger.info(f"Searching Gmail: {criteria}")
    status, data = conn.search(None, criteria)
    if status != "OK" or not data[0]:
        _logger.info("No unread FORM export emails found")
        return []
    msg_ids = [mid.decode() for mid in data[0].split()]
    _logger.info(f"Found {len(msg_ids)} unread FORM export email(s)")
    return msg_ids


def fetch_email(conn: imaplib.IMAP4_SSL, msg_id: str) -> Message:
    """Fetch and parse a single email by IMAP message ID.

    Args:
        conn: An open, authenticated IMAP connection.
        msg_id: IMAP message ID string as returned by search_form_emails().

    Returns:
        Parsed email.message.Message object.

    Raises:
        RuntimeError: If the IMAP fetch command fails.
    """
    status, data = conn.fetch(msg_id, "(RFC822)")
    if status != "OK":
        raise RuntimeError(f"IMAP fetch failed for message {msg_id} (status: {status})")
    return email.message_from_bytes(data[0][1])


def extract_s3_url(msg: Message) -> Optional[str]:
    """Extract the S3 presigned download URL from a FORM export email.

    Walks the email MIME parts to find the HTML body, then parses it
    to locate the 'Download Data' anchor tag and return its href.

    Args:
        msg: Parsed email message from fetch_email().

    Returns:
        The S3 presigned URL string, or None if not found.
    """
    html_body: Optional[str] = None

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    html_body = payload.decode("utf-8", errors="replace")
                break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            html_body = payload.decode("utf-8", errors="replace")

    if not html_body:
        _logger.error("No HTML body found in FORM email")
        return None

    parser = _DownloadLinkExtractor()
    parser.feed(html_body)

    if not parser.download_url:
        _logger.error("No 'Download Data' link found in FORM email HTML body")
        return None

    # Log a truncated URL so we don't flood logs with the full presigned query string
    _logger.info(f"Extracted S3 URL: {parser.download_url[:80]}...")
    return parser.download_url


def mark_as_read(conn: imaplib.IMAP4_SSL, msg_id: str) -> None:
    """Mark an email as read (\Seen) so it is excluded from future searches.

    This is called only after the full pipeline succeeds, so a failed run
    leaves the email unread and it will be retried on the next poll.

    Args:
        conn: An open, authenticated IMAP connection.
        msg_id: IMAP message ID to mark as read.
    """
    conn.store(msg_id, "+FLAGS", "\\Seen")
    _logger.info(f"Marked email {msg_id} as read")


def disconnect(conn: imaplib.IMAP4_SSL) -> None:
    """Close the IMAP connection gracefully.

    Safe to call even if the connection is already in a bad state.

    Args:
        conn: The IMAP connection to close.
    """
    try:
        conn.close()
        conn.logout()
        _logger.info("Gmail IMAP connection closed")
    except Exception:
        pass
