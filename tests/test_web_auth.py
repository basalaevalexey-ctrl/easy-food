import hashlib
import hmac
import unittest

from app.web_auth import (
    WEB_SESSION_COOKIE,
    VK_OAUTH_COOKIE,
    build_session_cookie,
    build_vk_oauth_cookie,
    clear_session_cookie,
    clear_vk_oauth_cookie,
    create_web_session,
    create_vk_oauth_flow,
    parse_web_session,
    parse_vk_oauth_flow,
    verify_telegram_login,
)


BOT_TOKEN = "123456:test-token"
SESSION_SECRET = "test-session-secret"


def signed_telegram_login(**values: str) -> dict[str, str]:
    data = {key: str(value) for key, value in values.items()}
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hashlib.sha256(BOT_TOKEN.encode("utf-8")).digest()
    data["hash"] = hmac.new(
        secret_key, check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return data


class TelegramWebLoginTests(unittest.TestCase):
    def test_accepts_valid_recent_login(self) -> None:
        values = signed_telegram_login(
            id="42",
            first_name="Алексей",
            username="alex",
            auth_date="1000",
        )

        user = verify_telegram_login(values, BOT_TOKEN, now=1200)

        self.assertIsNotNone(user)
        self.assertEqual(user["id"], 42)
        self.assertEqual(user["first_name"], "Алексей")
        self.assertEqual(user["_auth_mode"], "web")

    def test_rejects_tampered_login(self) -> None:
        values = signed_telegram_login(id="42", first_name="Алексей", auth_date="1000")
        values["id"] = "43"

        self.assertIsNone(verify_telegram_login(values, BOT_TOKEN, now=1200))

    def test_rejects_expired_login(self) -> None:
        values = signed_telegram_login(id="42", first_name="Алексей", auth_date="1000")

        self.assertIsNone(verify_telegram_login(values, BOT_TOKEN, now=2000))


class WebSessionTests(unittest.TestCase):
    def test_round_trips_signed_session(self) -> None:
        token = create_web_session(
            {"id": 42, "first_name": "Алексей", "username": "alex"},
            SESSION_SECRET,
            now=1000,
            ttl_seconds=300,
        )

        user = parse_web_session(token, SESSION_SECRET, now=1100)

        self.assertIsNotNone(user)
        self.assertEqual(user["id"], 42)
        self.assertEqual(user["first_name"], "Алексей")
        self.assertEqual(user["_auth_mode"], "web")
        self.assertEqual(user["_auth_provider"], "telegram")

    def test_round_trips_vk_session_provider(self) -> None:
        token = create_web_session(
            {"id": 8_000_000_000_000_000_001, "_auth_provider": "vk"},
            SESSION_SECRET,
            now=1000,
        )

        user = parse_web_session(token, SESSION_SECRET, now=1100)

        self.assertIsNotNone(user)
        self.assertEqual(user["_auth_provider"], "vk")

    def test_rejects_tampered_session(self) -> None:
        token = create_web_session({"id": 42}, SESSION_SECRET, now=1000)
        payload, signature = token.split(".", 1)
        tampered = f"{payload[:-1]}A.{signature}"

        self.assertIsNone(parse_web_session(tampered, SESSION_SECRET, now=1100))

    def test_rejects_expired_session(self) -> None:
        token = create_web_session(
            {"id": 42}, SESSION_SECRET, now=1000, ttl_seconds=100
        )

        self.assertIsNone(parse_web_session(token, SESSION_SECRET, now=1100))

    def test_rejects_malformed_session(self) -> None:
        self.assertIsNone(parse_web_session("not-base64.%%%", SESSION_SECRET, now=1100))

    def test_session_cookies_are_secure(self) -> None:
        cookie = build_session_cookie("signed-token", max_age=60)

        self.assertIn(f"{WEB_SESSION_COOKIE}=signed-token", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Max-Age=60", cookie)
        self.assertIn("Max-Age=0", clear_session_cookie())


class VKOAuthFlowTests(unittest.TestCase):
    def test_round_trips_oauth_state_and_verifier(self) -> None:
        state, verifier, challenge, token = create_vk_oauth_flow(
            SESSION_SECRET, now=1000
        )

        flow = parse_vk_oauth_flow(
            token, SESSION_SECRET, expected_state=state, now=1100
        )

        self.assertIsNotNone(flow)
        self.assertEqual(flow["verifier"], verifier)
        self.assertTrue(challenge)

    def test_rejects_wrong_or_expired_state(self) -> None:
        state, _, _, token = create_vk_oauth_flow(SESSION_SECRET, now=1000)

        self.assertIsNone(
            parse_vk_oauth_flow(token, SESSION_SECRET, expected_state="wrong", now=1100)
        )
        self.assertIsNone(
            parse_vk_oauth_flow(token, SESSION_SECRET, expected_state=state, now=1700)
        )

    def test_vk_flow_cookies_are_secure_and_scoped(self) -> None:
        cookie = build_vk_oauth_cookie("flow-token", max_age=60)

        self.assertIn(f"{VK_OAUTH_COOKIE}=flow-token", cookie)
        self.assertIn("Path=/web/auth/vk", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Max-Age=0", clear_vk_oauth_cookie())


if __name__ == "__main__":
    unittest.main()
