"""
notifier.py — Sends email alerts to every active team member via Gmail SMTP.

Uses Python's built-in smtplib — no external service, no approval process,
no template restrictions. Works the moment you have a Gmail app password.

SETUP (one-time, ~3 minutes):
  1. Turn on 2-Step Verification on the Gmail account you'll send FROM:
     https://myaccount.google.com/security
  2. Generate an App Password (NOT your normal Gmail password):
     https://myaccount.google.com/apppasswords
     -> Select app: "Mail", select device: "Other", name it "SIH Tracker"
     -> Copy the 16-character password it gives you (no spaces needed).
  3. Put these in your .env file:
       EMAIL_ADDRESS=youraccount@gmail.com
       EMAIL_APP_PASSWORD=<the 16-char app password>
  4. Register each team member's email:
       py -c "import db; db.add_team_member('Visshaal', 'you@gmail.com')"
     (repeat for each teammate, any email provider works for RECEIVING —
     only the SENDING account needs to be Gmail)
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

import db

# Load variables from .env into os.environ. Without this, os.environ.get()
# below would never see anything you put in .env -- python-dotenv doesn't
# auto-load files, it has to be called explicitly, and it must happen
# BEFORE the os.environ.get() calls below since those run at import time.
load_dotenv()

EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS", "")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))


def send_email(to_address, subject, body_html):
    if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD:
        print("[notifier] EMAIL_ADDRESS / EMAIL_APP_PASSWORD not set — skipping send. (Set them in .env)")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = to_address
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, to_address, msg.as_string())
        return True
    except smtplib.SMTPAuthenticationError:
        print(
            "[notifier] Gmail auth failed — make sure EMAIL_APP_PASSWORD is an "
            "App Password (not your normal Gmail password) and 2-Step "
            "Verification is enabled on the account."
        )
        return False
    except (smtplib.SMTPException, OSError) as e:
        print(f"[notifier] Email send failed for {to_address}: {e}")
        return False


def format_event_subject(event):
    delta = event["delta"]
    sign = "+" if delta > 0 else ""
    return f"[SIH Alert] {event['ps_id']}: {sign}{delta} new submission(s)"


def format_event_body(event):
    delta = event["delta"]
    emoji = "🔥" if delta >= 5 else "🚨"
    sign = "+" if delta > 0 else ""
    return f"""
    <html><body style="font-family: sans-serif;">
      <h2>{emoji} SIH Submission Alert</h2>
      <p><b>PS:</b> {event['ps_id']}</p>
      <p><b>Title:</b> {event['title']}</p>
      <p><b>New submissions:</b> {sign}{delta}</p>
      <p><b>Previous:</b> {event['previous_count']} &rarr; <b>Current:</b> {event['new_count']}</p>
      <p style="color:#888;"><b>Detected:</b> {event['detected_at']}</p>
    </body></html>
    """


def notify_all(event):
    """Send one event to every active team member with email enabled."""
    members = db.get_active_team_members()
    if not members:
        print("[notifier] No team members registered yet — nothing sent.")
        return

    subject = format_event_subject(event)
    body = format_event_body(event)
    sent_count = 0
    for member in members:
        if member["email_enabled"] and member["email"]:
            ok = send_email(member["email"], subject, body)
            if ok:
                sent_count += 1
    print(f"[notifier] Sent '{event['ps_id']}' alert to {sent_count}/{len(members)} members.")


if __name__ == "__main__":
    # quick manual test
    test_event = {
        "ps_id": "SIH26001",
        "title": "AI-Based early warning and landslide Risk Monitoring System in NER",
        "previous_count": 7,
        "new_count": 9,
        "delta": 2,
        "detected_at": "test",
    }
    notify_all(test_event)