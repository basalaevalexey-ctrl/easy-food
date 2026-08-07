import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping
from typing import Any


WEB_SESSION_COOKIE = "nyammetr_web_session"
VK_OAUTH_COOKIE = "nyammetr_vk_oauth"
WEB_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
TELEGRAM_LOGIN_MAX_AGE_SECONDS = 10 * 60
VK_OAUTH_TTL_SECONDS = 10 * 60


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _session_key(secret: str) -> bytes:
    return hashlib.sha256(f"nyammetr-web-session:{secret}".encode("utf-8")).digest()


def _sign_payload(payload: Mapping[str, Any], secret: str, namespace: str) -> str:
    encoded_payload = _base64url_encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    key = hashlib.sha256(f"nyammetr:{namespace}:{secret}".encode("utf-8")).digest()
    signature = hmac.new(key, encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded_payload}.{_base64url_encode(signature)}"


def _parse_signed_payload(token: str, secret: str, namespace: str) -> dict[str, Any] | None:
    if not token or not secret:
        return None
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        received_signature = _base64url_decode(encoded_signature)
        key = hashlib.sha256(f"nyammetr:{namespace}:{secret}".encode("utf-8")).digest()
        expected_signature = hmac.new(
            key, encoded_payload.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected_signature, received_signature):
            return None
        payload = json.loads(_base64url_decode(encoded_payload).decode("utf-8"))
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def verify_telegram_login(
    values: Mapping[str, str],
    bot_token: str,
    *,
    now: int | None = None,
    max_age_seconds: int = TELEGRAM_LOGIN_MAX_AGE_SECONDS,
) -> dict[str, Any] | None:
    if not bot_token:
        return None

    data = {str(key): str(value) for key, value in values.items()}
    received_hash = data.pop("hash", "")
    if not received_hash:
        return None

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    expected_hash = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        return None

    try:
        telegram_id = int(data.get("id", "0"))
        auth_date = int(data.get("auth_date", "0"))
    except ValueError:
        return None
    current_time = int(time.time() if now is None else now)
    if telegram_id <= 0 or auth_date <= 0:
        return None
    if auth_date > current_time + 60 or current_time - auth_date > max_age_seconds:
        return None

    return {
        "id": telegram_id,
        "first_name": data.get("first_name", ""),
        "last_name": data.get("last_name", ""),
        "username": data.get("username", ""),
        "photo_url": data.get("photo_url", ""),
        "auth_date": auth_date,
        "_auth_mode": "web",
    }


def create_web_session(
    telegram_user: Mapping[str, Any],
    secret: str,
    *,
    now: int | None = None,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
) -> str:
    if not secret:
        raise ValueError("web_session_secret_missing")
    current_time = int(time.time() if now is None else now)
    payload = {
        "id": int(telegram_user["id"]),
        "first_name": str(telegram_user.get("first_name") or ""),
        "last_name": str(telegram_user.get("last_name") or ""),
        "username": str(telegram_user.get("username") or ""),
        "photo_url": str(telegram_user.get("photo_url") or ""),
        "provider": str(telegram_user.get("_auth_provider") or "telegram"),
        "iat": current_time,
        "exp": current_time + ttl_seconds,
    }
    encoded_payload = _base64url_encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        _session_key(secret), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded_payload}.{_base64url_encode(signature)}"


def parse_web_session(
    token: str,
    secret: str,
    *,
    now: int | None = None,
) -> dict[str, Any] | None:
    if not token or not secret:
        return None
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        received_signature = _base64url_decode(encoded_signature)
        expected_signature = hmac.new(
            _session_key(secret), encoded_payload.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected_signature, received_signature):
            return None
        payload = json.loads(_base64url_decode(encoded_payload).decode("utf-8"))
        telegram_id = int(payload.get("id", 0))
        expires_at = int(payload.get("exp", 0))
        issued_at = int(payload.get("iat", 0))
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    current_time = int(time.time() if now is None else now)
    if telegram_id <= 0 or issued_at <= 0 or expires_at <= current_time or issued_at > current_time + 60:
        return None
    return {
        "id": telegram_id,
        "first_name": str(payload.get("first_name") or ""),
        "last_name": str(payload.get("last_name") or ""),
        "username": str(payload.get("username") or ""),
        "photo_url": str(payload.get("photo_url") or ""),
        "_auth_mode": "web",
        "_auth_provider": str(payload.get("provider") or "telegram"),
    }


def create_vk_oauth_flow(
    secret: str,
    *,
    now: int | None = None,
) -> tuple[str, str, str, str]:
    if not secret:
        raise ValueError("web_session_secret_missing")
    current_time = int(time.time() if now is None else now)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = _base64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    token = _sign_payload(
        {
            "state": state,
            "verifier": verifier,
            "iat": current_time,
            "exp": current_time + VK_OAUTH_TTL_SECONDS,
        },
        secret,
        "vk-oauth",
    )
    return state, verifier, challenge, token


def parse_vk_oauth_flow(
    token: str,
    secret: str,
    *,
    expected_state: str,
    now: int | None = None,
) -> dict[str, str] | None:
    payload = _parse_signed_payload(token, secret, "vk-oauth")
    if not payload:
        return None
    current_time = int(time.time() if now is None else now)
    try:
        issued_at = int(payload.get("iat", 0))
        expires_at = int(payload.get("exp", 0))
    except (TypeError, ValueError):
        return None
    state = str(payload.get("state") or "")
    verifier = str(payload.get("verifier") or "")
    if (
        not state
        or not verifier
        or not hmac.compare_digest(state, expected_state)
        or issued_at <= 0
        or issued_at > current_time + 60
        or expires_at <= current_time
    ):
        return None
    return {"state": state, "verifier": verifier}


def build_session_cookie(token: str, *, max_age: int = WEB_SESSION_TTL_SECONDS) -> str:
    return (
        f"{WEB_SESSION_COOKIE}={token}; Path=/; Max-Age={max_age}; "
        "HttpOnly; Secure; SameSite=Lax"
    )


def clear_session_cookie() -> str:
    return (
        f"{WEB_SESSION_COOKIE}=; Path=/; Max-Age=0; "
        "HttpOnly; Secure; SameSite=Lax"
    )


def build_vk_oauth_cookie(token: str, *, max_age: int = VK_OAUTH_TTL_SECONDS) -> str:
    return (
        f"{VK_OAUTH_COOKIE}={token}; Path=/web/auth/vk; Max-Age={max_age}; "
        "HttpOnly; Secure; SameSite=Lax"
    )


def clear_vk_oauth_cookie() -> str:
    return (
        f"{VK_OAUTH_COOKIE}=; Path=/web/auth/vk; Max-Age=0; "
        "HttpOnly; Secure; SameSite=Lax"
    )
