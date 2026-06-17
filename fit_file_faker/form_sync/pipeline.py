"""Main pipeline: Gmail → FORM archive → FIT → Garmin Connect.

Entry point for the GitHub Actions workflow. Reads all configuration from
environment variables (populated by GitHub Secrets), then processes any
unread FORM export emails found in Gmail.

Each email is processed atomically:
  1. Fetch email, extract S3 URL
  2. Download ZIP archive, extract .fit files
  3. Rewrite each .fit to appear as the configured Garmin device
  4. Upload to Garmin Connect
  5. Persist refreshed Garmin tokens back to GitHub Secrets
  6. Mark email as read

The email is only marked as read after all steps succeed. A failure at any
earlier step leaves the email unread so the next scheduled run retries it
automatically — with one exception: an expired S3 link (403) cannot be
retried, so the email is marked read to prevent infinite retry loops, and
the error is logged for manual review.

Environment variables (all required unless noted):
    FFF_GMAIL_ADDRESS           Gmail address to monitor
    FFF_GMAIL_APP_PASSWORD      Google App Password for IMAP access
    FFF_GARMIN_EMAIL            Garmin Connect account email
    FFF_GARMIN_PASSWORD         Garmin Connect account password
    FFF_GARMIN_TOKENS           Base64 token bundle (optional; empty on first run)
    FFF_GH_PAT                  GitHub PAT with secrets:write scope
    GITHUB_REPOSITORY           Set automatically by GitHub Actions (owner/repo)
    FFF_GARMIN_SERIAL_NUMBER    Device Unit ID matching the physical Garmin device
    FFF_GARMIN_DEVICE_ID        Garmin product ID (default: 4315 = Forerunner 965).
                                Software version is looked up automatically from the
                                device registry; no separate version secret needed.
"""

import logging
import os
import tempfile
from pathlib import Path

import requests

from fit_file_faker.config import SUPPLEMENTAL_GARMIN_DEVICES
from fit_file_faker.form_sync import downloader, garmin, gmail
from fit_file_faker.form_sync import github as gh
from fit_file_faker.form_sync.processor import build_profile, process_fit_file

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    """Read a required environment variable or raise a clear error."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set. "
            "Add it as a GitHub Secret and reference it in the workflow YAML."
        )
    return value


_DEFAULT_SOFTWARE_VERSION = 2709  # Forerunner 965 v27.09, matches default device_id 4315


def _lookup_software_version(device_id: int) -> int:
    """Return the latest known firmware version for a Garmin product ID.

    Looks up the device in the supplemental registry. Falls back to the
    default Forerunner 965 firmware if the device isn't found or has no
    version recorded.
    """
    for device in SUPPLEMENTAL_GARMIN_DEVICES:
        if device.product_id == device_id and device.software_version is not None:
            _logger.info(
                f"Device ID {device_id} ({device.name}): "
                f"using firmware version {device.software_version}"
            )
            return device.software_version
    _logger.warning(
        f"Device ID {device_id} not found in registry or has no firmware version — "
        f"falling back to {_DEFAULT_SOFTWARE_VERSION}"
    )
    return _DEFAULT_SOFTWARE_VERSION


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run() -> None:
    """Run the full FORM → Garmin sync pipeline.

    Called by the GitHub Actions workflow as:
        python -m fit_file_faker.form_sync.pipeline

    Exits cleanly (no exception) whether or not any emails were processed,
    so the workflow always reports success unless there is an unexpected error.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # --- Load configuration from environment (GitHub Secrets) ---------------
    gmail_address       = _require_env("FFF_GMAIL_ADDRESS")
    gmail_app_password  = _require_env("FFF_GMAIL_APP_PASSWORD")
    garmin_email        = _require_env("FFF_GARMIN_EMAIL")
    garmin_password     = _require_env("FFF_GARMIN_PASSWORD")
    garmin_tokens_b64   = os.environ.get("FFF_GARMIN_TOKENS", "").strip()
    gh_pat              = _require_env("FFF_GH_PAT")
    gh_repo             = _require_env("GITHUB_REPOSITORY")
    serial_number       = int(_require_env("FFF_GARMIN_SERIAL_NUMBER"))
    device_id           = int(os.environ.get("FFF_GARMIN_DEVICE_ID", "4315"))
    software_version    = _lookup_software_version(device_id)

    # --- Check Gmail for unread FORM export emails --------------------------
    conn = gmail.connect(gmail_address, gmail_app_password)
    try:
        msg_ids = gmail.search_form_emails(conn)
        if not msg_ids:
            _logger.info("No new FORM export emails — nothing to do")
            return

        for msg_id in msg_ids:
            garmin_tokens_b64 = _process_email(
                conn=conn,
                msg_id=msg_id,
                garmin_email=garmin_email,
                garmin_password=garmin_password,
                garmin_tokens_b64=garmin_tokens_b64,
                device_id=device_id,
                serial_number=serial_number,
                software_version=software_version,
                gh_pat=gh_pat,
                gh_repo=gh_repo,
            )
    finally:
        gmail.disconnect(conn)


# ---------------------------------------------------------------------------
# Per-email processing
# ---------------------------------------------------------------------------

def _process_email(
    conn,
    msg_id: str,
    garmin_email: str,
    garmin_password: str,
    garmin_tokens_b64: str,
    device_id: int,
    serial_number: int,
    software_version: int,
    gh_pat: str,
    gh_repo: str,
) -> str:
    """Process a single FORM export email end-to-end.

    Returns the (possibly refreshed) Garmin token bundle so the caller can
    pass it into the next iteration without making redundant API calls.
    """
    _logger.info(f"--- Processing email {msg_id} ---")

    # Step 1: Extract the S3 download URL from the email body
    msg = gmail.fetch_email(conn, msg_id)
    s3_url = gmail.extract_s3_url(msg)
    if not s3_url:
        _logger.error(f"Email {msg_id}: could not extract S3 URL — skipping")
        gmail.mark_as_read(conn, msg_id)
        return garmin_tokens_b64

    profile = build_profile(
        garmin_email=garmin_email,
        garmin_password=garmin_password,
        device_id=device_id,
        serial_number=serial_number,
        software_version=software_version,
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Step 2: Download ZIP archive and extract FIT files
        try:
            zip_path = downloader.download_zip(s3_url, tmp_path)
        except requests.HTTPError as e:
            if "403" in str(e) or (hasattr(e, "response") and e.response is not None and e.response.status_code == 403):
                # Presigned URL has expired — can't retry, mark read to stop looping
                _logger.error(
                    f"Email {msg_id}: S3 link has expired (403). "
                    "Marking as read to prevent retry loops. "
                    "Export again from the FORM app."
                )
                gmail.mark_as_read(conn, msg_id)
            else:
                _logger.error(f"Email {msg_id}: download failed — {e}")
            return garmin_tokens_b64

        fit_files = downloader.extract_fit_files(zip_path, tmp_path)
        if not fit_files:
            _logger.error(f"Email {msg_id}: no .fit files in archive — skipping")
            gmail.mark_as_read(conn, msg_id)
            return garmin_tokens_b64

        # Step 3 + 4: Rewrite each FIT file and upload to Garmin Connect
        for fit_path in fit_files:
            modified_path = process_fit_file(fit_path, profile)
            garmin_tokens_b64 = garmin.upload_fit(
                fit_path=modified_path,
                email=garmin_email,
                password=garmin_password,
                tokens_b64=garmin_tokens_b64,
            )

    # Step 5: Persist refreshed tokens to GitHub Secrets
    gh.update_secret(gh_repo, "FFF_GARMIN_TOKENS", garmin_tokens_b64, gh_pat)

    # Step 6: Mark email as read — only reached on full success
    gmail.mark_as_read(conn, msg_id)
    _logger.info(f"--- Email {msg_id} complete ---")

    return garmin_tokens_b64


if __name__ == "__main__":
    run()
