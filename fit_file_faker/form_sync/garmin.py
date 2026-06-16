"""Garmin Connect upload and session token management.

Handles authentication against Garmin Connect and FIT file upload,
with token persistence designed for stateless cloud execution.

Token lifecycle:
    1. Tokens arrive as a base64-encoded JSON bundle from the GARMIN_TOKENS
       GitHub Secret (empty string on first run).
    2. They are decoded and written to a temporary directory before login.
    3. garminconnect loads and transparently refreshes them via garth.
    4. After upload, updated token files are re-encoded and returned to the
       caller, which persists them back to the GitHub Secret for the next run.

If stored tokens are absent or rejected, the client falls back to a fresh
username/password login. A fresh login may trigger Garmin's MFA flow, which
cannot be completed in a headless environment — this is surfaced as a clear
error rather than silently hanging.
"""

import base64
import json
import logging
import tempfile
from pathlib import Path

from garminconnect import Garmin, GarminConnectConnectionError

_logger = logging.getLogger(__name__)

# Token files written by the garth library (garminconnect's auth backend)
_TOKEN_FILES = ["oauth1_token.json", "oauth2_token.json"]


def decode_tokens(tokens_b64: str, token_dir: Path) -> None:
    """Decode a base64 token bundle and write individual token files to disk.

    Args:
        tokens_b64: Base64-encoded JSON string produced by encode_tokens().
        token_dir: Directory to write the token files into.
    """
    bundle = json.loads(base64.b64decode(tokens_b64).decode("utf-8"))
    for filename, content in bundle.items():
        (token_dir / filename).write_text(json.dumps(content))
    _logger.info(f"Loaded {len(bundle)} Garmin token file(s) from secret")


def encode_tokens(token_dir: Path) -> str:
    """Read garth token files and return a base64-encoded JSON bundle.

    Args:
        token_dir: Directory containing garth token files.

    Returns:
        Base64-encoded JSON string suitable for storage as a GitHub Secret.
    """
    bundle: dict = {}
    for filename in _TOKEN_FILES:
        token_file = token_dir / filename
        if token_file.exists():
            bundle[filename] = json.loads(token_file.read_text())

    if not bundle:
        _logger.warning("No token files found to encode — token bundle will be empty")

    return base64.b64encode(json.dumps(bundle).encode("utf-8")).decode("utf-8")


def upload_fit(
    fit_path: Path,
    email: str,
    password: str,
    tokens_b64: str,
) -> str:
    """Upload a modified FIT file to Garmin Connect.

    Authenticates using stored tokens if available, falling back to a fresh
    password-based login. After a successful upload, returns an updated
    token bundle so the caller can persist refreshed tokens.

    Duplicate uploads (HTTP 409) are treated as success — the activity is
    already on Garmin Connect, so the pipeline should still mark the email
    as read and update tokens.

    Args:
        fit_path: Path to the modified .fit file to upload.
        email: Garmin Connect account email.
        password: Garmin Connect account password.
        tokens_b64: Base64-encoded token bundle from the GARMIN_TOKENS secret.
            Pass an empty string on first run.

    Returns:
        Updated base64-encoded token bundle to persist back to GitHub Secrets.

    Raises:
        GarminConnectConnectionError: For non-409 upload failures.
        RuntimeError: If fresh login is attempted in a headless environment
            and Garmin requires MFA interaction.
    """
    with tempfile.TemporaryDirectory() as tmp:
        token_dir = Path(tmp)
        client = Garmin(email, password)

        if tokens_b64:
            try:
                decode_tokens(tokens_b64, token_dir)
                client.login(tokenstore=str(token_dir))
                _logger.info("Authenticated via stored Garmin tokens")
            except Exception as e:
                _logger.warning(
                    f"Stored token login failed ({e}) — attempting fresh password login. "
                    "If MFA is required this will fail; re-run seed_garmin_tokens.py locally."
                )
                client.login()
        else:
            _logger.info("No stored tokens — performing fresh Garmin login")
            client.login()

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
                raise

        # Persist refreshed tokens back to disk so we can re-encode them
        try:
            client.garth.dump(str(token_dir))
        except Exception:
            # Older garminconnect versions save to tokenstore automatically on login
            pass

        updated_tokens = encode_tokens(token_dir)

    return updated_tokens
