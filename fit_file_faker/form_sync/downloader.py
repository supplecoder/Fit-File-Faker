"""Download FORM export archive from S3 and extract FIT files.

FORM export emails contain a presigned S3 URL that points to a ZIP archive.
The link expires after 48 hours. With 30-minute polling this is never an issue
in practice, but we surface a clear error if the link has expired rather than
silently failing.

The archive may contain one or more .fit files. All are extracted and returned
so the pipeline can process each one independently.
"""

import logging
import zipfile
from pathlib import Path

import requests

_logger = logging.getLogger(__name__)


def download_zip(url: str, dest_dir: Path) -> Path:
    """Download the FORM export ZIP archive from an S3 presigned URL.

    Args:
        url: Presigned S3 URL from the FORM export email. Expires in 48 hours.
        dest_dir: Directory to write the downloaded archive into.

    Returns:
        Path to the downloaded ZIP file.

    Raises:
        requests.HTTPError: If the download fails (including 403 if link expired).
    """
    _logger.info("Downloading FORM export archive from S3")
    response = requests.get(url, timeout=60)

    if response.status_code == 403:
        raise requests.HTTPError(
            "S3 presigned URL returned 403 — the download link has likely expired "
            "(links expire after 48 hours). The email will remain unread for manual review.",
            response=response,
        )

    if not response.ok:
        # S3 returns a descriptive XML body on error (e.g. AuthorizationQuery
        # ParametersError, RequestTimeTooSkewed). Surface it so the real cause
        # is visible in the logs instead of a bare status code.
        body = (response.text or "")[:1000]
        _logger.error(
            f"S3 download failed with HTTP {response.status_code}. "
            f"Response body:\n{body}"
        )

    response.raise_for_status()

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
