r"""Checks the SMTP settings in .env by logging in to the mail server. Sends NO email and prints no password.

    cd backend
    .\.venv\Scripts\python.exe scripts\smtp_check.py

It says which of the usual problems you have: settings missing, a normal Gmail password instead of an app password, the network
cutting the connection, or all fine."""
from __future__ import annotations

import smtplib
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402


def main() -> int:
    s = get_settings()
    if not (s.smtp_host and s.smtp_user and s.smtp_password):
        print("NOT READY: SMTP_HOST, SMTP_USER and SMTP_PASSWORD must all be set in .env (and the file saved).")
        return 1
    pw = s.smtp_password.replace(" ", "")
    print(f"server {s.smtp_host}:{s.smtp_port}, user {s.smtp_user}, password length {len(pw)}")
    if s.smtp_host.endswith("gmail.com") and not (len(pw) == 16 and pw.isalpha()):
        print("WARNING: a Gmail app password is exactly 16 letters (no digits or symbols). This looks like a normal password, "
              "which Gmail refuses for SMTP. Make one at https://myaccount.google.com/apppasswords (needs 2-step verification).")
    try:
        if s.smtp_port == 465:
            c = smtplib.SMTP_SSL(s.smtp_host, 465, timeout=20, context=ssl.create_default_context())
        else:
            c = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
            c.ehlo()
            c.starttls(context=ssl.create_default_context())
            c.ehlo()
        c.login(s.smtp_user, s.smtp_password)
        c.close()
    except smtplib.SMTPAuthenticationError as e:
        print(f"REFUSED by the mail server ({e.smtp_code}): the user or password is wrong. For Gmail use an app password.")
        return 2
    except (smtplib.SMTPException, OSError) as e:
        print(f"CONNECTION PROBLEM ({type(e).__name__}): the mail server was reached but the connection was cut. "
              "This is your network (router, ISP, VPN, firewall), not the password. Try a phone hotspot.")
        return 3
    print("LOGIN OK: the app can send email with these settings. Restart the backend if you have just changed .env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
