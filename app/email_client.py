from email.message import EmailMessage
from html import escape
import smtplib
import ssl


class EmailDeliveryError(RuntimeError):
    pass


def send_password_reset_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    from_email: str,
    from_name: str,
    recipient: str,
    reset_url: str,
    use_ssl: bool = True,
    use_tls: bool = False,
) -> None:
    if not smtp_host or not from_email:
        raise EmailDeliveryError("smtp_not_configured")

    safe_url = escape(reset_url, quote=True)
    message = EmailMessage()
    message["Subject"] = "Восстановление пароля Нямметра"
    message["From"] = f"{from_name} <{from_email}>"
    message["To"] = recipient
    message.set_content(
        "Ты запросил восстановление пароля Нямметра.\n\n"
        f"Открой ссылку: {reset_url}\n\n"
        "Ссылка действует 30 минут. Если это был не ты, просто проигнорируй письмо."
    )
    message.add_alternative(
        f"""
        <!doctype html>
        <html lang="ru"><body style="margin:0;background:#f7f5ed;font-family:Arial,sans-serif;color:#1b1b1b">
          <div style="max-width:520px;margin:32px auto;padding:32px;background:#fff;border-radius:16px">
            <h1 style="margin:0 0 12px;font-size:24px">Восстановление пароля</h1>
            <p style="line-height:1.5">Нажми кнопку, чтобы задать новый пароль Нямметра.</p>
            <p style="margin:24px 0"><a href="{safe_url}" style="display:inline-block;padding:14px 22px;border-radius:10px;background:#58bd35;color:#fff;text-decoration:none;font-weight:700">Задать новый пароль</a></p>
            <p style="color:#737373;font-size:13px;line-height:1.5">Ссылка действует 30 минут. Если это был не ты, просто проигнорируй письмо.</p>
          </div>
        </body></html>
        """,
        subtype="html",
    )

    context = ssl.create_default_context()
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15, context=context) as client:
                if smtp_username:
                    client.login(smtp_username, smtp_password)
                client.send_message(message)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as client:
                if use_tls:
                    client.starttls(context=context)
                if smtp_username:
                    client.login(smtp_username, smtp_password)
                client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError("email_delivery_failed") from exc
