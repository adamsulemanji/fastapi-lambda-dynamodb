"""
One-time (or when tokens expire) Garmin Connect login for the /runs API.

Run locally, in your own terminal, *after* the stack is deployed (the deploy
creates the garmin/tokens secret):
    python3.13 -m venv .venv && .venv/bin/pip install garminconnect boto3
    .venv/bin/python scripts/garmin_login.py

Prompts for your Garmin email, password and MFA code (if enabled). The password
is only sent to Garmin. The resulting session tokens are cached locally in
~/.garminconnect (owner-only) so reruns don't log in again (Garmin rate-limits
logins), and are copied into Secrets Manager for the Lambda. Uses your default
AWS profile (override with AWS_PROFILE / AWS_REGION). Pass --fresh to force a
new login.
"""

import getpass
import os
import sys
from pathlib import Path

import boto3
from garminconnect import Garmin

SECRET_ID = os.environ.get("GARMIN_SECRET_ID", "garmin/tokens")
TOKENSTORE = str(Path(os.environ.get("GARMINTOKENS", "~/.garminconnect")).expanduser())


def _login() -> Garmin:
    if "--fresh" not in sys.argv and Path(TOKENSTORE, "garmin_tokens.json").exists():
        try:
            garmin = Garmin()
            garmin.login(tokenstore=TOKENSTORE)
            print(f"Reused saved Garmin session from {TOKENSTORE}.")
            return garmin
        except Exception as exc:
            print(f"Saved session unusable ({type(exc).__name__}); logging in again.")

    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (not stored): ")
    garmin = Garmin(email, password, prompt_mfa=lambda: input("Garmin MFA code: ").strip())
    garmin.login(tokenstore=TOKENSTORE)  # also writes the tokens to TOKENSTORE
    return garmin


def main() -> int:
    garmin = _login()
    tokens = garmin.client.dumps()

    secrets = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    try:
        secrets.put_secret_value(SecretId=SECRET_ID, SecretString=tokens)
    except secrets.exceptions.ResourceNotFoundException:
        print(
            f"\nGarmin login worked, but the '{SECRET_ID}' secret doesn't exist yet.\n"
            "Deploy the stack first (npx cdk deploy), then rerun this script; it will\n"
            f"reuse the session saved in {TOKENSTORE} without asking for your password."
        )
        return 1
    print(f"Saved Garmin tokens to Secrets Manager secret '{SECRET_ID}'.")

    # Quick sanity check of what the API will see (no tokens printed).
    for a in garmin.get_activities(0, 5) or []:
        print(" -", a.get("startTimeLocal"), (a.get("activityType") or {}).get("typeKey"),
              a.get("activityName"), f"{(a.get('distance') or 0) / 1609.344:.2f} mi",
              "gps" if a.get("hasPolyline") else "no-gps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
