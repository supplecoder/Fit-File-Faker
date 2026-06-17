"""Garmin Connect upload and session token management.

Handles authentication against Garmin Connect and FIT file upload,
with token persistence designed for stateless cloud execution.

Token lifecycle:
    1. A token string arrives from the FFF_GARMIN_TOKENS GitHub Secret
       (empty string on first run).
    2. garminconnect loads it directly via login(tokenstore=<string>) and
       transparently refreshes the session.
    3. After upload, the refreshed token string is returned to the caller,
       which persists it back to the GitHub Secret for the next run.

The token string is whatever garth's `dumps()` produces — an opaque,
self-contained bundle of the OAuth1/OAuth2 tokens. We never parse it.

If the stored token is absent or rejected, the client falls back to a fresh
username/password login. A fresh login may trigger Garmin's MFA flow, which
cannot be completed in a headless environment — this is surfaced as a clear
error rather than silently hanging.
"""

import logging
from pathlib import Path

from garminconnect import Garmin, GarminConnectConnectionError

from fit_file_faker.form_sync.errors import GarminAuthError, TransientError

_logger = logging.getLogger(__name__)


def upload_fit(
    fit_path: Path,
    email: str,
    password: str,
    tokens: str,
) -> str:
    """Upload a modified FIT file to Garmin Connect.

    Authenticates using a stored token string if available, falling back to a
    fresh password-based login. After a successful upload, returns the refreshed
    token string so the caller can persist it.

    Duplicate uploads (HTTP 409) are treated as success — the activity is
    already on Garmin Connect, so the pipeline should still mark the email
    as read and update tokens.

    Args:
        fit_path: Path to the modified .fit file to upload.
        email: Garmin Connect account email.
        password: Garmin Connect account password.
        tokens: Token string from the FFF_GARMIN_TOKENS secret (produced by
            garth's dumps()). Pass an empty string on first run.

    Returns:
        Refreshed token string to persist back to GitHub Secrets.

    Raises:
        GarminAuthError: If authentication fails (token rejected + fresh login
            fails). Needs a local token re-seed; not retryable headlessly.
        TransientError: For non-409 upload failures (likely server-side).
    """
    client = Garmin(email, password)

    # garminconnect treats a tokenstore longer than 512 chars as token data
    # passed directly (rather than a filesystem path), so we can hand it the
    # secret string verbatim.
    try:
        if tokens and len(tokens) > 512:
            try:
                client.login(tokenstore=tokens)
                _logger.info("Authenticated via stored Garmin token")
            except Exception as e:
                _logger.warning(
                    f"Stored token login failed ({e}) — attempting fresh password login. "
                    "If MFA is required this will fail; re-run seed_garmin_tokens.py locally."
                )
                client.login()
        else:
            _logger.info("No stored token — performing fresh Garmin login")
            client.login()
    except Exception as e:
        raise GarminAuthError(
            f"Garmin authentication failed: {e}"
        ) from e

    try:
        _logger.info(f"Uploading {fit_path.name} to Garmin Connect")
        client.upload_activity(str(fit_path))
        _logger.info(f"Successfully uploaded {fit_path.name}")
    except GarminConnectConnectionError as e:
        if "409" in str(e):
            _logger.warning(
                f"Garmin Connect returned 409 for {fit_path.name} — "
                "activity already exists, continuing"
            )
        else:
            # Upload failures are typically server-side/transient (rate limits,
            # outages) — flag as retryable so the email is left unread.
            raise TransientError(
                f"Garmin upload failed for {fit_path.name}: {e}"
            ) from e

    # Return the refreshed token string for persistence
    return client.client.dumps()
