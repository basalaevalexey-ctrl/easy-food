import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"


class PrivacyPolicyTests(unittest.TestCase):
    def test_policy_is_public_and_contains_contact(self) -> None:
        policy = (PUBLIC / "privacy.html").read_text(encoding="utf-8")

        self.assertIn("Политика обработки персональных данных", policy)
        self.assertIn("nyammetr@yandex.ru", policy)
        self.assertIn("OpenAI API", policy)

    def test_auth_screens_link_to_policy(self) -> None:
        for name in ("index.html", "nyammetr-live.html"):
            with self.subTest(name=name):
                html = (PUBLIC / name).read_text(encoding="utf-8")
                self.assertIn('class="web-auth-privacy"', html)
                self.assertIn('href="/privacy"', html)


if __name__ == "__main__":
    unittest.main()
