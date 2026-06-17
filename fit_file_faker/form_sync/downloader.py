"""Download FORM export archive from S3 and extract FIT files.

FORM export emails contain a presigned S3 URL that points to a ZIP archive.
The URL's query string advertises a 48-hour expiry (X-Amz-Expires), but FORM
signs it with temporary AWS STS credentials (an "ASIA..." access key) that
expire much sooner — often within ~1 hour. So the link's real lifespan is the
shorter of the two. With 30-minute polling a fresh export is normally fetched
well within that window; an older export may already be dead (HTTP 400
"ExpiredToken"), in which case it must be re-exported from the FORM app.

SECURITY: presigned URLs embed a temporary credential and signature in the
query string. On a public repo, Actions logs are world-readable, so we never
log the full URL or the S3 error body verbatim (the body echoes the security
token). Only the host+path and the S3 error <Code>/<Message> are logged.

The archive may contain one or more .fit files. All are extracted and returned
so the pipeline can process each one independently.
"""

import logging
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import requests

_logger = logging.getLogger(__name__)


def _redact_url(url: str) -> str:
    """Return scheme://host/path with the (sensitive) query string removed."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _parse_s3_error(body: str) -> str:
    """Extract just the <Code> and <Message> from an S3 XML error response.

    Avoids logging the full body, which echoes the request's security token
    (e.g. inside a <Token-0> element).
    """
    code = re.search(r"<Code>(.*?)</Code>", body or "")
    message = re.search(r"<Message>(.*?)</Message>", body or "")
    code_str = code.group(1) if code else "Unknown"
    message_str = message.group(1) if message else "(no message)"
    return f"{code_str}: {message_str}"


def download_zip(url: str, dest_dir: Path) -> Path:
    """Download the FORM export ZIP archive from an S3 presigned URL.

    Args:
        url: Presigned S3 URL from the FORM export email.
        dest_dir: Directory to write the downloaded archive into.

    Returns:
        Path to the downloaded ZIP file.

    Raises:
        RuntimeError: If the download fails. The message includes the S3 error
            code/message and a redacted URL, never the security token.
    """
    _logger.info(f"Downloading FORM export archive from {_redact_url(url)}")
    response = requests.get(url, timeout=60)

    if not response.ok:
        # S3 returns descriptive XML on error (e.g. ExpiredToken,
        # AuthorizationQueryParametersError). Parse out only Code/Message so
        # we never log the security token echoed back in the body.
        s3_error = _parse_s3_error(response.text)
        raise RuntimeError(
            f"S3 download failed with HTTP {response.status_code} "
            f"[{s3_error}] for {_redact_url(url)}. "
            "If this is 'ExpiredToken' the FORM link's temporary credentials "
            "have expired — re-export from the FORM app to get a fresh link."
        )

    zip_path = dest_dir / "form_export.zip"
    zip_path.write_bytes(response.content)
    _logger.info(f"Downloaded {len(response.content):,} bytes → {zip_path.name}")
    return zip_path


def extract_fit_files(zip_path: Path, dest_dir: Path) -> list[Path]:
    """Extract all .fit files from the FORM export ZIP archive.

    Args:
        zip_path: Path to the downloaded ZIP archive.
        dest_dir: Directory to extract FIT files into.

    Returns:
        List of Paths to extracted .fit files. Empty list if none found.
    """
    fit_files: list[Path] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        all_names = zf.namelist()
        fit_names = [n for n in all_names if n.lower().endswith(".fit")]
        _logger.info(
            f"Archive contains {len(all_names)} file(s), {len(fit_names)} .fit file(s)"
        )

        for name in fit_names:
            extracted_path = Path(zf.extract(name, dest_dir))
            fit_files.append(extracted_path)
            _logger.info(f"Extracted: {name}")

    if not fit_files:
        _logger.error("No .fit files found in the FORM export archive")

    return fit_files
