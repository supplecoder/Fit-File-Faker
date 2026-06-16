"""Seed Garmin tokens for the form_sync GitHub Actions pipeline.

Run this script ONCE locally before setting up the GitHub Actions workflow.
It authenticates interactively with Garmin Connect (handling any MFA prompts),
captures the garth session tokens, and prints a base64-encoded bundle you can
store as the GARMIN_TOKENS GitHub Secret.

Usage:
    python scripts/seed_garmin_tokens.py

You will be prompted for your Garmin email and password. If Garmin requires
MFA, follow the prompts. The output is printed to stdout — copy it into your
GitHub repository's Secrets as GARMIN_TOKENS.

Requirements:
    pip install -e ".[form_sync]"
    (or just: pip install garminconnect)
"""

import base64
import getpass
import json
import sys
import tempfile
from pathlib import Path

try:
    from garminconnect import Garmin
except ImportError:
    print(
        "garminconnect is not installed.\n"
        'Run: pip install -e ".[form_sync]"',
        file=sys.stderr,
    )
    sys.exit(1)

_TOKEN_FILES = ["oauth1_token.json", "oauth2_token.json"]


def main() -> None:
    print("=== Garmin Connect Token Seeder ===")
    print("This script authenticates with Garmin Connect and produces a")
    print("base64-encoded token bundle for the GARMIN_TOKENS GitHub Secret.\n")

    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")

    if not email or not password:
        print("Email and password are required.", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        token_dir = Path(tmp)
        client = Garmin(email, password)

        print("\nAuthenticating with Garmin Connect...")
        print("(If MFA is required, follow the prompts below)\n")

        try:
            client.login()
        except Exception as e:
            print(f"\nAuthentication failed: {e}", file=sys.stderr)
            sys.exit(1)

        print("\nAuthentication successful.")

        # Save fresh tokens to disk
        try:
            client.garth.dump(str(token_dir))
        except Exception:
            # Older garminconnect saves automatically on login
            pass

        bundle: dict = {}
        for filename in _TOKEN_FILES:
            token_file = token_dir / filename
            if token_file.exists():
                bundle[filename] = json.loads(token_file.read_text())

        if not bundle:
            print(
                "\nNo token files were written by garminconnect.",
                "Check that garth is installed and working correctly.",
                file=sys.stderr,
            )
            sys.exit(1)

        encoded = base64.b64encode(json.dumps(bundle).encode("utf-8")).decode("utf-8")

    print("\n" + "=" * 60)
    print("GARMIN_TOKENS secret value (copy everything between the lines):")
    print("=" * 60)
    print(encoded)
    print("=" * 60)
    print(
        "\nGo to your GitHub repository → Settings → Secrets and variables"
        " → Actions → New repository secret"
        "\nName: GARMIN_TOKENS"
        "\nValue: (paste the string above)"
    )


if __name__ == "__main__":
    main()
