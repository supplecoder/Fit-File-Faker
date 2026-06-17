"""Tests for fit_file_faker.form_sync.github (Secrets API)."""

import base64
from unittest.mock import MagicMock, patch

import pytest
from nacl import encoding, public

from fit_file_faker.form_sync import github as gh


def _make_keypair():
    """Return (private_key, base64_public_key) for round-trip decryption."""
    priv = public.PrivateKey.generate()
    pub_b64 = priv.public_key.encode(encoding.Base64Encoder()).decode()
    return priv, pub_b64


class TestEncryptSecret:
    def test_roundtrip_decrypts_to_original(self):
        priv, pub_b64 = _make_keypair()
        encrypted_b64 = gh._encrypt_secret(pub_b64, "super-secret-value")

        # Decrypt with the private key to prove the encryption is valid
        sealed = base64.b64decode(encrypted_b64)
        decrypted = public.SealedBox(priv).decrypt(sealed)
        assert decrypted.decode() == "super-secret-value"

    def test_output_is_base64(self):
        _, pub_b64 = _make_keypair()
        out = gh._encrypt_secret(pub_b64, "x")
        # Should decode without error
        base64.b64decode(out)


class TestUpdateSecret:
    def test_fetches_key_then_puts_encrypted(self):
        _, pub_b64 = _make_keypair()

        get_resp = MagicMock()
        get_resp.json.return_value = {"key_id": "kid-123", "key": pub_b64}
        get_resp.raise_for_status = MagicMock()

        put_resp = MagicMock()
        put_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=get_resp) as mock_get, \
             patch("requests.put", return_value=put_resp) as mock_put:
            gh.update_secret("owner/repo", "FFF_GARMIN_TOKENS", "tokenval", "pat")

        # Pulled the repo public key
        assert "public-key" in mock_get.call_args[0][0]
        # PUT to the secret endpoint with an encrypted payload + key id
        put_url = mock_put.call_args[0][0]
        assert put_url.endswith("/actions/secrets/FFF_GARMIN_TOKENS")
        body = mock_put.call_args.kwargs["json"]
        assert body["key_id"] == "kid-123"
        assert "encrypted_value" in body
        # Never sends the plaintext
        assert body["encrypted_value"] != "tokenval"

    def test_raises_on_api_error(self):
        get_resp = MagicMock()
        get_resp.raise_for_status.side_effect = Exception("401")
        with patch("requests.get", return_value=get_resp):
            with pytest.raises(Exception):
                gh.update_secret("owner/repo", "S", "v", "pat")
