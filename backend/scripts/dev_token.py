"""Fetch a dev access token from the local Keycloak — the sanctioned local-auth path.

app/core/auth.py has no bypass flag on purpose ("The friction is the point"): local dev
authenticates against the real OIDC issuer, not a magic string. This just automates the
password-grant curl the dev realm already documents, so the frontend (or a curl) can get a
real RS256 token with a real clinic_id claim.

    python scripts/dev_token.py                # prints the access token
    python scripts/dev_token.py --header       # prints: Authorization: Bearer <token>
    TOKEN=$(python scripts/dev_token.py)       # use it

Needs `docker compose up keycloak` running (port 8081). The user, client, and password are
the fictional dev-realm values in keycloak/realm-luxmedicine.json — dev only, never prod.
"""

from __future__ import annotations

import argparse
import sys

import httpx

TOKEN_URL = "http://localhost:8081/realms/luxmedicine/protocol/openid-connect/token"
FORM = {
    "grant_type": "password",
    "client_id": "luxmedicine-api",
    "username": "dr.smith",
    "password": "dev-password",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--header",
        action="store_true",
        help="print a full Authorization header instead of the bare token",
    )
    args = parser.parse_args()

    try:
        response = httpx.post(TOKEN_URL, data=FORM, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(
            f"could not reach Keycloak at {TOKEN_URL}: {exc}\n"
            "is `docker compose up keycloak` running?",
            file=sys.stderr,
        )
        return 1

    token = response.json().get("access_token")
    if not token:
        print(f"no access_token in response: {response.text[:200]}", file=sys.stderr)
        return 1

    print(f"Authorization: Bearer {token}" if args.header else token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
