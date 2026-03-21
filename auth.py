import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("ALLEGRO_CLIENT_ID")
CLIENT_SECRET = os.getenv("ALLEGRO_CLIENT_SECRET")
TOKENS_FILE = "tokens.json"

DEVICE_URL = "https://allegro.pl/auth/oauth/device"
TOKEN_URL = "https://allegro.pl/auth/oauth/token"


def authorize():
    response = requests.post(
        DEVICE_URL,
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={"client_id": CLIENT_ID},
    )
    response.raise_for_status()
    return response.json()


def poll_for_token(device_code, interval):
    response = requests.post(
        TOKEN_URL,
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
        },
    )
    data = response.json()

    if "error" in data:
        if data["error"] == "authorization_pending":
            return None
        raise RuntimeError(f"Token error: {data.get('error_description', data['error'])}")

    data["created_at"] = time.time()
    _save_tokens(data)
    return data


def get_token():
    if not os.path.exists(TOKENS_FILE):
        return None

    with open(TOKENS_FILE) as f:
        tokens = json.load(f)

    expires_at = tokens.get("created_at", 0) + tokens.get("expires_in", 0)
    if time.time() >= expires_at:
        return refresh(tokens.get("refresh_token"))

    return tokens.get("access_token")


def refresh(refresh_token=None):
    if refresh_token is None:
        if not os.path.exists(TOKENS_FILE):
            return None
        with open(TOKENS_FILE) as f:
            tokens = json.load(f)
        refresh_token = tokens.get("refresh_token")

    if not refresh_token:
        return None

    try:
        response = requests.post(
            TOKEN_URL,
            auth=(CLIENT_ID, CLIENT_SECRET),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        response.raise_for_status()
        data = response.json()
        data["created_at"] = time.time()
        _save_tokens(data)
        return data.get("access_token")
    except Exception:
        return None


def _save_tokens(data):
    with open(TOKENS_FILE, "w") as f:
        json.dump(data, f)
