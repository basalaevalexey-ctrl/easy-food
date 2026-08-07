import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


VK_AUTHORIZE_URL = "https://id.vk.ru/authorize"
VK_TOKEN_URL = "https://id.vk.ru/oauth2/auth"
VK_USER_INFO_URL = "https://id.vk.ru/oauth2/user_info"


class VKAuthError(RuntimeError):
    pass


def build_vk_authorize_url(
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    if not client_id or not redirect_uri:
        raise VKAuthError("vk_not_configured")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "scope": "email",
        }
    )
    return f"{VK_AUTHORIZE_URL}?{query}"


def _post_form(
    url: str,
    data: dict[str, str],
    *,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    request = Request(
        url,
        data=urlencode(data).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Nyammetr-Web/1.0",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise VKAuthError("vk_request_failed") from exc
    if not isinstance(payload, dict) or payload.get("error"):
        raise VKAuthError(str(payload.get("error") or "vk_invalid_response"))
    return payload


def exchange_vk_code(
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    device_id: str,
    state: str,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code": code,
        "code_verifier": code_verifier,
        "device_id": device_id,
        "state": state,
    }
    if client_secret:
        form["client_secret"] = client_secret
    payload = _post_form(VK_TOKEN_URL, form, opener=opener)
    if not payload.get("access_token"):
        raise VKAuthError("vk_access_token_missing")
    return payload


def fetch_vk_user(
    *,
    client_id: str,
    access_token: str,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, str]:
    payload = _post_form(
        VK_USER_INFO_URL,
        {"client_id": client_id, "access_token": access_token},
        opener=opener,
    )
    raw_user = payload.get("user") if isinstance(payload.get("user"), dict) else payload
    provider_user_id = str(raw_user.get("user_id") or raw_user.get("id") or "").strip()
    if not provider_user_id:
        raise VKAuthError("vk_user_id_missing")
    return {
        "provider_user_id": provider_user_id,
        "first_name": str(raw_user.get("first_name") or ""),
        "last_name": str(raw_user.get("last_name") or ""),
        "photo_url": str(raw_user.get("avatar") or raw_user.get("avatar_200") or ""),
        "email": str(raw_user.get("email") or ""),
    }
