import unittest

from app.password_auth import hash_password, normalize_email, validate_password, verify_password


class PasswordAuthTests(unittest.TestCase):
    def test_normalizes_email(self) -> None:
        self.assertEqual(normalize_email("  User@Example.COM "), "user@example.com")

    def test_rejects_invalid_email(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid_email"):
            normalize_email("not-an-email")

    def test_rejects_short_password(self) -> None:
        with self.assertRaisesRegex(ValueError, "password_too_short"):
            validate_password("short")

    def test_hashes_and_verifies_password(self) -> None:
        encoded = hash_password("correct horse battery staple")

        self.assertNotIn("correct horse", encoded)
        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong password", encoded))

    def test_uses_unique_salts(self) -> None:
        first = hash_password("same-password")
        second = hash_password("same-password")

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
