import unittest
from unittest.mock import patch

from app.email_client import EmailDeliveryError, send_password_reset_email


class EmailClientTests(unittest.TestCase):
    def test_requires_smtp_configuration(self) -> None:
        with self.assertRaisesRegex(EmailDeliveryError, "smtp_not_configured"):
            send_password_reset_email(
                smtp_host="",
                smtp_port=465,
                smtp_username="",
                smtp_password="",
                from_email="",
                from_name="Нямметр",
                recipient="person@example.com",
                reset_url="https://app.nyammetr.ru/web?reset_token=test",
            )

    @patch("app.email_client.smtplib.SMTP_SSL")
    def test_sends_reset_link_over_ssl(self, smtp_ssl) -> None:
        client = smtp_ssl.return_value.__enter__.return_value

        send_password_reset_email(
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="mailer",
            smtp_password="secret",
            from_email="hello@example.com",
            from_name="Нямметр",
            recipient="person@example.com",
            reset_url="https://app.nyammetr.ru/web?reset_token=abc",
        )

        client.login.assert_called_once_with("mailer", "secret")
        message = client.send_message.call_args.args[0]
        self.assertEqual(message["To"], "person@example.com")
        self.assertIn("reset_token=abc", message.get_body(preferencelist=("plain",)).get_content())


if __name__ == "__main__":
    unittest.main()
