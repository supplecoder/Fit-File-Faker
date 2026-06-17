"""Seed the Garmin token for the form_sync GitHub Actions pipeline.

Run this script ONCE locally before setting up the GitHub Actions workflow.
It authenticates interactively with Garmin Connect (handling any MFA prompts),
captures the garth session token, and prints a token string you can store as
the FFF_GARMIN_TOKENS GitHub Secret.

Usage:
    python scripts/seed_garmin_tokens.py

You will be prompted for your Garmin email and password. If Garmin requires
MFA, follow the prompts. The output is printed to stdout — copy it into your
GitHub repository's Secrets as FFF_GARMIN_TOKENS.

Requirements:
    pip install -e ".[form_sync]"
    (or just: pip install garminconnect)
"""

import getpass
import sys


def main() -> None:
    print("=== Garmin Connect Token Seeder ===")
    print("This script authenticates with Garmin Connect and produces a")
    print("token string for the FFF_GARMIN_TOKENS GitHub Secret.\n")

    try:
        from garminconnect import Garmin
    except ImportError:
        print(
            "garminconnect is not installed.\n"
            'Run: pip install -e ".[form_sync]"',
            file=sys.stderr,
        )
        sys.exit(1)

    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")

    if not email or not password:
        print("Email and password are required.", file=sys.stderr)
        sys.exit(1)

    client = Garmin(email, password)

    print("\nAuthenticating with Garmin Connect...")
    print("(If MFA is required, follow the prompts below)\n")

    try:
        client.login()
    except Exception as e:
        print(f"\nAuthentication failed: {e}", file=sys.stderr)
        sys.exit(1)

    # garth's dumps() returns a single opaque token string containing the
    # OAuth1/OAuth2 tokens. This is exactly what login(tokenstore=...) accepts.
    token = client.client.dumps()

    if not token or len(token) <= 512:
        print(
            "\nLogin reported success but no usable token was produced.\n"
            "This can happen if Garmin rate-limited the request (HTTP 429).\n"
            "Wait a few minutes and try again.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\nAuthentication successful.")
    print("\n" + "=" * 60)
    print("FFF_GARMIN_TOKENS secret value (copy everything between the lines):")
    print("=" * 60)
    print(token)
    print("=" * 60)
    print(
        "\nGo to your GitHub repository → Settings → Secrets and variables"
        " → Actions → New repository secret"
        "\nName: FFF_GARMIN_TOKENS"
        "\nValue: (paste the string above)"
    )


if __name__ == "__main__":
    main()
