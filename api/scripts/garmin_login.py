"""
One-time (or when tokens expire) Garmin Connect login for the /runs API.

Run locally, in your own terminal:
    python3.13 -m venv .venv && .venv/bin/pip install garminconnect boto3
    .venv/bin/python scripts/garmin_login.py

Prompts for your Garmin email, password and MFA code (if enabled). The password
is only sent to Garmin; what gets stored in Secrets Manager is the session
token JSON that the Lambda uses and refreshes. Uses your default AWS profile
(override with AWS_PROFILE / AWS_REGION).
"""

import getpass
import json
import os
import sys

import boto3
from garminconnect import Garmin

SECRET_ID = os.environ.get("GARMIN_SECRET_ID", "garmin/tokens")


def main() -> int:
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (not stored): ")
    garmin = Garmin(email, password, prompt_mfa=lambda: input("Garmin MFA code: ").strip())
    garmin.login()
    tokens = garmin.client.dumps()

    secrets = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    secrets.put_secret_value(SecretId=SECRET_ID, SecretString=tokens)
    print(f"Saved Garmin tokens to Secrets Manager secret '{SECRET_ID}'.")

    # Quick sanity check of what the API will see (no tokens printed).
    activities = garmin.get_activities(0, 5) or []
    for a in activities:
        print(" -", a.get("startTimeLocal"), (a.get("activityType") or {}).get("typeKey"),
              a.get("activityName"), f"{(a.get('distance') or 0) / 1609.344:.2f} mi",
              "gps" if a.get("hasPolyline") else "no-gps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
