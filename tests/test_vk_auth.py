import json
import unittest
from urllib.parse import parse_qs, urlparse

from app.vk_auth import build_vk_authorize_url, exchange_vk_code, fetch_vk_user


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class VKAuthTests(unittest.TestCase):
    def test_authorize_url_contains_pkce_and_callback(self) -> None:
        url = build_vk_authorize_url("123", "https://example.com/callback", "state", "challenge")
        query = parse_qs(urlparse(url).query)

        self.assertEqual(query["client_id"], ["123"])
        self.assertEqual(query["state"], ["state"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], ["https://example.com/callback"])

    def test_exchanges_code_with_pkce(self) -> None:
        captured = {}

        def opener(request, timeout):
            captured["data"] = parse_qs(request.data.decode("utf-8"))
            return FakeResponse({"access_token": "token"})

        result = exchange_vk_code(
            client_id="123",
            client_secret="secret",
            redirect_uri="https://example.com/callback",
            code="code",
            code_verifier="verifier",
            device_id="device",
            state="state",
            opener=opener,
        )

        self.assertEqual(result["access_token"], "token")
        self.assertEqual(captured["data"]["code_verifier"], ["verifier"])
        self.assertEqual(captured["data"]["device_id"], ["device"])

    def test_reads_vk_profile(self) -> None:
        def opener(_request, timeout):
            return FakeResponse(
                {"user": {"user_id": "42", "first_name": "Алексей", "avatar": "https://img"}}
            )

        user = fetch_vk_user(client_id="123", access_token="token", opener=opener)

        self.assertEqual(user["provider_user_id"], "42")
        self.assertEqual(user["first_name"], "Алексей")
        self.assertEqual(user["photo_url"], "https://img")


if __name__ == "__main__":
    unittest.main()
