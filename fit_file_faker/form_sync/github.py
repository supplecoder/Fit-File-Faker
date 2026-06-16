"""GitHub Actions Secrets API — persist Garmin tokens between workflow runs.

Garmin session tokens are refreshed on every login. To avoid a full
re-authentication (which may require MFA) on each run, we write the
updated token bundle back to the GARMIN_TOKENS GitHub Secret after
every successful pipeline execution.

GitHub requires secrets to be encrypted with the repo's libsodium public
key before transmission. PyNaCl handles the sealed-box encryption.
"""

import base64
import logging

import requests
from nacl import encoding, public

_logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def _get_repo_public_key(repo: str, headers: dict) -> tuple[str, str]:
    """Fetch the repository's public key used to encrypt secrets.

    Args:
        repo: Repository in 'owner/name' format (e.g. 'supplecoder/fit-file-faker').
        headers: Authenticated request headers.

    Returns:
        Tuple of (key_id, base64-encoded public key).

    Raises:
        requests.HTTPError: If the API request fails.
    """
    resp = requests.get(
        f"{GITHUB_API}/repos/{repo}/actions/secrets/public-key",
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["key_id"], data["key"]


def _encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """Encrypt a secret value using a libsodium sealed box.

    GitHub requires this specific encryption scheme for secret values
    submitted via the REST API.

    Args:
        public_key_b64: Base64-encoded repository public key from the API.
        secret_value: Plaintext secret value to encrypt.

    Returns:
        Base64-encoded encrypted secret value.
    """
    pk = public.PublicKey(public_key_b64.encode(), encoding.Base64Encoder())
    box = public.SealedBox(pk)
    encrypted = box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def update_secret(repo: str, secret_name: str, secret_value: str, gh_pat: str) -> None:
    """Create or update a GitHub Actions secret in the given repository.

    Used to persist the refreshed Garmin token bundle back to GARMIN_TOKENS
    so the next workflow run can authenticate without a fresh login.

    Args:
        repo: Repository in 'owner/name' format. In GitHub Actions this is
            the GITHUB_REPOSITORY environment variable.
        secret_name: Name of the secret to create or update (e.g. 'GARMIN_TOKENS').
        secret_value: Plaintext value to store. Will be encrypted before transmission.
        gh_pat: Personal Access Token with 'secrets' write scope.

    Raises:
        requests.HTTPError: If the API request fails (bad PAT, wrong repo, etc.).
    """
    headers = {
        "Authorization": f"Bearer {gh_pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    key_id, public_key = _get_repo_public_key(repo, headers)
    encrypted_value = _encrypt_secret(public_key, secret_value)

    resp = requests.put(
        f"{GITHUB_API}/repos/{repo}/actions/secrets/{secret_name}",
        headers=headers,
        json={"encrypted_value": encrypted_value, "key_id": key_id},
        timeout=10,
    )
    resp.raise_for_status()
    _logger.info(f"GitHub secret '{secret_name}' updated successfully")
