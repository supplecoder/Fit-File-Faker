"""Main pipeline: Gmail → FORM archive → FIT → Garmin Connect.

Entry point for the GitHub Actions workflow. Reads all configuration from
environment variables (populated by GitHub Secrets), then processes any
unread FORM export emails found in Gmail.

Each email is processed independently:
  1. Fetch email, extract S3 URL
  2. Download ZIP archive, extract .fit files
  3. Rewrite each .fit to appear as the configured Garmin device
  4. Upload to Garmin Connect
  5. Persist refreshed Garmin tokens back to GitHub Secrets
  6. Mark email as read

Failure handling philosophy: an email is marked as read ONLY after all steps
succeed. Any failure leaves the email unread (so it stays visible and is
retried on the next run) and causes the workflow to exit non-zero, which
triggers a GitHub Actions failure notification. Emails are processed
independently, so one failing email does not prevent newer emails from being
processed — but if any email fails, the overall run still fails so the
failure is never silent.

Note: if an email is genuinely unprocessable (e.g. its S3 link has expired
after 48 hours), it will keep failing on every run until you re-export from
the FORM app or manually mark/delete the email. This is intentional — loud,
visible failures are preferred over silently skipping data.

Environment variables (all required unless noted):
    FFF_GMAIL_ADDRESS           Gmail address to monitor
    FFF_GMAIL_APP_PASSWORD      Google App Password for IMAP access
    FFF_GARMIN_EMAIL            Garmin Connect account email
    FFF_GARMIN_PASSWORD         Garmin Connect account password
    FFF_GARMIN_TOKENS           Garmin session token string (optional; empty on first run)
    FFF_GH_PAT                  GitHub PAT with secrets:write scope
    GITHUB_REPOSITORY           Set automatically by GitHub Actions (owner/repo)
    FFF_GARMIN_UNIT_ID          Unit ID of the physical Garmin device (Settings →
                                About → Unit ID). Named "serial_number" in the FIT
                                spec, but is NOT the printed serial number.
    FFF_GARMIN_DEVICE_ID        Garmin product ID (e.g. 4315 = Forerunner 965).
                                Software version is looked up automatically from the
                                device registry; no separate version secret needed.
"""

import logging
import os
import tempfile
from pathlib import Path

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


def _lookup_software_version(device_id: int) -> int:
    """Return the latest known firmware version for a Garmin product ID.

    Looks up the device in the supplemental registry. Raises if the device
    ID is not found — check FFF_GARMIN_DEVICE_ID is a valid Garmin product ID.
    """
    for device in SUPPLEMENTAL_GARMIN_DEVICES:
        if device.product_id == device_id and device.software_version is not None:
            _logger.info(
                f"Device ID {device_id} ({device.name}): "
                f"using firmware version {device.software_version}"
            )
            return device.software_version
    raise RuntimeError(
        f"Device ID {device_id} not found in the Garmin device registry or has no "
        "firmware version recorded. Check that FFF_GARMIN_DEVICE_ID is a valid "
        "Garmin product ID (e.g. 4315 for Forerunner 965)."
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run() -> None:
    """Run the full FORM → Garmin sync pipeline.

    Called by the GitHub Actions workflow as:
        python -m fit_file_faker.form_sync.pipeline

    Exits cleanly when there is nothing to do or every email succeeds. If any
    email fails to process, raises at the end so the workflow exits non-zero
    and GitHub sends a failure notification.
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
    garmin_tokens   = os.environ.get("FFF_GARMIN_TOKENS", "").strip()
    gh_pat              = _require_env("FFF_GH_PAT")
    gh_repo             = _require_env("GITHUB_REPOSITORY")
    # FIT spec calls this field "serial_number" but it holds the Unit ID, not the printed serial
    serial_number       = int(_require_env("FFF_GARMIN_UNIT_ID"))
    device_id           = int(_require_env("FFF_GARMIN_DEVICE_ID"))
    software_version    = _lookup_software_version(device_id)

    # --- Check Gmail for unread FORM export emails --------------------------
    conn = gmail.connect(gmail_address, gmail_app_password)
    failed_ids: list[str] = []
    try:
        msg_ids = gmail.search_form_emails(conn)
        if not msg_ids:
            _logger.info("No new FORM export emails — nothing to do")
            return

        # Process each email independently. A failure on one email is recorded
        # and we continue to the next, so one bad email never blocks newer ones.
        # The email is left unread on failure so it stays visible and retries.
        for msg_id in msg_ids:
            try:
                garmin_tokens = _process_email(
                    conn=conn,
                    msg_id=msg_id,
                    garmin_email=garmin_email,
                    garmin_password=garmin_password,
                    garmin_tokens=garmin_tokens,
                    device_id=device_id,
                    serial_number=serial_number,
                    software_version=software_version,
                    gh_pat=gh_pat,
                    gh_repo=gh_repo,
                )
            except Exception:
                # Log full traceback, leave the email unread, keep going
                _logger.exception(f"Email {msg_id} failed to process")
                failed_ids.append(msg_id)
    finally:
        gmail.disconnect(conn)

    # If any email failed, fail the whole run so GitHub Actions sends a
    # failure notification. Successful emails have already been marked read.
    if failed_ids:
        raise RuntimeError(
            f"{len(failed_ids)} of the FORM email(s) failed to process: "
            f"{failed_ids}. They were left unread and will be retried on the "
            "next run. Check the logs above for the cause."
        )


# ---------------------------------------------------------------------------
# Per-email processing
# ---------------------------------------------------------------------------

def _process_email(
    conn,
    msg_id: str,
    garmin_email: str,
    garmin_password: str,
    garmin_tokens: str,
    device_id: int,
    serial_number: int,
    software_version: int,
    gh_pat: str,
    gh_repo: str,
) -> str:
    """Process a single FORM export email end-to-end.

    Raises on any failure (no .fit files, download error, upload error, etc.)
    so the caller can record the failure, leave the email unread, and fail the
    overall run. The email is marked as read only on full success.

    Returns the refreshed Garmin token string so the caller can pass it into
    the next iteration without making redundant API calls.
    """
    _logger.info(f"--- Processing email {msg_id} ---")

    # Step 1: Extract the S3 download URL from the email body
    msg = gmail.fetch_email(conn, msg_id)
    s3_url = gmail.extract_s3_url(msg)
    if not s3_url:
        raise RuntimeError(
            f"Email {msg_id}: could not extract an S3 download URL from the email body"
        )

    profile = build_profile(
        garmin_email=garmin_email,
        garmin_password=garmin_password,
        device_id=device_id,
        serial_number=serial_number,
        software_version=software_version,
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Step 2: Download ZIP archive and extract FIT files.
        # download_zip raises on any HTTP error (the S3 XML error body is
        # logged by the downloader for diagnosis); we let it propagate so the
        # email stays unread and the run fails loudly.
        zip_path = downloader.download_zip(s3_url, tmp_path)

        fit_files = downloader.extract_fit_files(zip_path, tmp_path)
        if not fit_files:
            raise RuntimeError(
                f"Email {msg_id}: no .fit files found in the downloaded FORM archive"
            )

        # Step 3 + 4: Rewrite each FIT file and upload to Garmin Connect
        for fit_path in fit_files:
            modified_path = process_fit_file(fit_path, profile)
            garmin_tokens = garmin.upload_fit(
                fit_path=modified_path,
                email=garmin_email,
                password=garmin_password,
                tokens=garmin_tokens,
            )

    # Step 5: Persist refreshed tokens to GitHub Secrets
    gh.update_secret(gh_repo, "FFF_GARMIN_TOKENS", garmin_tokens, gh_pat)

    # Step 6: Mark email as read — only reached on full success
    gmail.mark_as_read(conn, msg_id)
    _logger.info(f"--- Email {msg_id} complete ---")

    return garmin_tokens


if __name__ == "__main__":
    run()
