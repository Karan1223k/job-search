"""Google Sheet update and email delivery."""
from __future__ import annotations

import html
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

import config


def update_sheet(jobs) -> int:
    """Send the matching companies to the sheet's Apps Script web app (apps_script.gs), which appends
    each company name only if it is not already listed. Returns the number of companies added."""
    companies = list(dict.fromkeys(j.company for j in jobs))
    r = requests.post(os.environ["SHEET_WEBAPP_URL"],
                      json={"token": os.environ["SHEET_TOKEN"], "companies": companies}, timeout=120)
    r.raise_for_status()
    result = r.json()
    if "error" in result:
        raise RuntimeError(f"sheet update failed: {result['error']}")
    return result["added"]


def send_email(top_jobs, total_matches: int) -> None:
    sender = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    sheet_url = f"https://docs.google.com/spreadsheets/d/{config.SHEET_ID}"

    if top_jobs:
        rows = "".join(
            f"<tr><td style='padding:6px 10px'>{i}</td>"
            f"<td style='padding:6px 10px'><b>{html.escape(j.company)}</b></td>"
            f"<td style='padding:6px 10px'><a href='{html.escape(j.url)}'>{html.escape(j.title)}</a></td>"
            f"<td style='padding:6px 10px'>{html.escape(j.location[:60])}</td></tr>"
            for i, j in enumerate(top_jobs, 1)
        )
        body = (f"<p>Top {len(top_jobs)} of {total_matches} new matching jobs from official career sites "
                f"(posted in the last {config.MAX_AGE_DAYS} days, minimum experience 0-{config.MAX_MIN_EXPERIENCE_YEARS} yr):</p>"
                f"<table border='1' style='border-collapse:collapse;font-family:sans-serif;font-size:14px'>"
                f"<tr><th>#</th><th>Company</th><th>Job</th><th>Location</th></tr>{rows}</table>")
    else:
        body = "<p>No new matching jobs were found today.</p>"
    body += f"<p><a href='{sheet_url}'>Open the company tracker sheet</a></p>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Daily job matches: {len(top_jobs)} new"
    msg["From"] = sender
    msg["To"] = config.EMAIL_TO
    plain = "\n".join(f"{i}. {j.company} - {j.title}\n   {j.url}" for i, j in enumerate(top_jobs, 1)) or "No new matching jobs today."
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(sender, password)
        s.sendmail(sender, [config.EMAIL_TO], msg.as_string())
