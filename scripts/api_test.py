"""Run basic, read-only checks against the FastAPI server."""

import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
AUTH_FAILURE_HINTS = {
    "invalid_credentials": "Check that the test account and password in .env match Supabase Auth for this project.",
    "email_not_confirmed": "Confirm the test account's email in Supabase Auth.",
    "email_provider_disabled": "Check the email provider settings in Supabase Auth.",
    "user_banned": "Check whether this test account is banned in Supabase Auth.",
    "captcha_failed": "Check whether the Supabase Auth project requires a CAPTCHA for sign-in.",
    "over_request_rate_limit": "Wait before retrying the login.",
}


class TestFailure(Exception):
    pass


def auth_failure_message(response: httpx.Response) -> str:
    message = f"Supabase Auth login failed (HTTP {response.status_code})."
    try:
        body = response.json()
    except ValueError:
        return message
    if not isinstance(body, dict):
        return message
    code = body.get("error_code") or body.get("code")
    if isinstance(code, str) and code in AUTH_FAILURE_HINTS:
        return f"{message} {code}: {AUTH_FAILURE_HINTS[code]}"
    return message


def get_access_token(client: httpx.Client) -> str:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")).strip()
    email = os.environ.get("API_TEST_EMAIL", "").strip()
    password = os.environ.get("API_TEST_PASSWORD", "")
    if not url or not key or key.startswith("your-"):
        raise TestFailure("Set SUPABASE_URL and a publishable or anon key in .env.")
    if not email or not password:
        raise TestFailure("Set API_TEST_EMAIL and API_TEST_PASSWORD in .env.")

    try:
        response = client.post(
            f"{url}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": key},
            json={"email": email, "password": password},
        )
    except (httpx.RequestError, httpx.InvalidURL):
        raise TestFailure("Could not reach Supabase Auth. Check SUPABASE_URL and the connection.") from None
    if not response.is_success:
        raise TestFailure(auth_failure_message(response))
    try:
        token = response.json()["access_token"]
    except (ValueError, KeyError, TypeError):
        raise TestFailure("Supabase Auth did not return an access token.") from None
    if not isinstance(token, str) or not token:
        raise TestFailure("Supabase Auth did not return an access token.")
    return token


def check_get(
    client: httpx.Client, base_url: str, path: str, expected_status: int, token: str | None = None,
) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    try:
        response = client.get(f"{base_url}{path}", headers=headers)
    except (httpx.RequestError, httpx.InvalidURL):
        raise TestFailure(f"GET {path} could not connect. Check that FastAPI is running at {base_url}.") from None
    label = f"GET {path}" + (" (with token)" if token else "")
    if response.status_code != expected_status:
        raise TestFailure(f"{label} returned HTTP {response.status_code}. Expected {expected_status}.")
    print(f"{label} -> {response.status_code}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"FastAPI base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--token-only", action="store_true", help="Print a Supabase access token for Swagger UI")
    args = parser.parse_args(argv)
    base_url = args.base_url.rstrip("/")
    load_dotenv(ENV_FILE, encoding="utf-8-sig", interpolate=False)

    try:
        with httpx.Client(timeout=15) as client:
            if args.token_only:
                print(get_access_token(client))
                return 0

            check_get(client, base_url, "/health", 200)
            check_get(client, base_url, "/api/v1/terms?limit=1", 200)
            check_get(client, base_url, "/api/v1/timetables", 401)

            token = get_access_token(client)
            check_get(client, base_url, "/api/v1/timetables", 200, token)
            check_get(client, base_url, "/api/v1/events?limit=1", 200, token)
    except TestFailure as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print("API smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
