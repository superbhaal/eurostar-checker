
# -*- coding: utf-8 -*-
"""
checker.py — hardened version for Railway

Key improvements:
- Email sending is resilient (IPv4-first, tries all resolved IPs, retries, alt host).
- Won't crash the job if email delivery fails (logs and continue).
- Optional HTTP email via SendGrid (port 443) to bypass SMTP egress blocks.
- Cleaner env var parsing + recipient handling.
- Hooks for your existing scraping logic.

Minimal expectations:
- Your existing code should call `send_email(available_entries)` with a list of offers.
  Each offer can be a dict with helpful fields like:
     {
       "date": "2025-10-14",
       "band": "morning" | "afternoon" | "evening" | "unknown",
       "time_from": "06:17",
       "time_to": "14:00",
       "price": 45,
       "route": "Paris → Amsterdam",
       "source": "Eurostar Snap",
       "url": "https://..."
     }
  Only `price` is truly required for display; others are optional.

- If you can't or don't want to use SMTP, set SENDGRID_API_KEY and the script
  will use HTTPS (port 443) automatically.

Environment variables you can set on Railway:
------------------------------------------------
EMAIL_SENDER          : sender email (e.g., your Gmail address)
EMAIL_PASSWORD        : app-specific password (for Gmail) if using SMTP
EMAIL_RECIPIENT       : comma-separated list of recipients
SMTP_SERVER           : default 'smtp.gmail.com'
SMTP_SERVER_ALT       : default 'smtp.googlemail.com'
SMTP_PORT             : default '587' (STARTTLS) or '465' (implicit SSL not used here)
FORCE_IPV4_ONLY       : '1' to skip IPv6 entirely (optional)
SENDGRID_API_KEY      : if set, HTTP email will be attempted first
EMAIL_FROM_NAME       : optional display name for the sender (default: 'Eurostar Snap Bot')
EMAIL_SUBJECT_PREFIX  : optional, e.g. '[Eurostar Snap] '
ROUTE_LABEL           : optional, e.g. 'Paris → Amsterdam' (used in subject)
"""

import os
import sys
import ssl
import time
import json
import socket
import datetime as dt
from email.mime.text import MIMEText
from email.utils import formataddr

try:
    # Optional; not required if using SMTP only. We avoid hard dependency by trying import lazily.
    import requests  # type: ignore
except Exception:
    requests = None  # We'll fallback to urllib if needed

import urllib.request
import urllib.error

# -----------------------------
# Logging helpers
# -----------------------------
def log(msg: str):
    now = dt.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[checker {now}Z] {msg}")

def log_mail(msg: str):
    log(f"[mail] {msg}")


# -----------------------------
# HTML builder for the email body
# -----------------------------
def build_email_html(available_entries):
    if not available_entries:
        return "<p>No availability detected.</p>"

    rows = []
    for e in available_entries:
        date = e.get("date", "-")
        band = e.get("band", "unknown")
        tfrom = e.get("time_from", "?")
        tto = e.get("time_to", "?")
        price = e.get("price", "?")
        src = e.get("source", "?")
        route = e.get("route", os.environ.get("ROUTE_LABEL", ""))
        url = e.get("url", "")

        if url:
            src_html = f'<a href="{url}">{src}</a>'
        else:
            src_html = src

        rows.append(
            f"<tr>"
            f"<td style='padding:6px;border:1px solid #ddd'>{date}</td>"
            f"<td style='padding:6px;border:1px solid #ddd'>{route}</td>"
            f"<td style='padding:6px;border:1px solid #ddd'>{band}</td>"
            f"<td style='padding:6px;border:1px solid #ddd'>{tfrom} – {tto}</td>"
            f"<td style='padding:6px;border:1px solid #ddd'>{price} €</td>"
            f"<td style='padding:6px;border:1px solid #ddd'>{src_html}</td>"
            f"</tr>"
        )

    html = (
        "<div>"
        "<h2>New availabilities detected</h2>"
        "<table style='border-collapse:collapse;font-family:Arial;font-size:14px'>"
        "<thead><tr>"
        "<th style='padding:6px;border:1px solid #ddd;text-align:left'>Date</th>"
        "<th style='padding:6px;border:1px solid #ddd;text-align:left'>Route</th>"
        "<th style='padding:6px;border:1px solid #ddd;text-align:left'>Band</th>"
        "<th style='padding:6px;border:1px solid #ddd;text-align:left'>Time</th>"
        "<th style='padding:6px;border:1px solid #ddd;text-align:left'>Price</th>"
        "<th style='padding:6px;border:1px solid #ddd;text-align:left'>Source</th>"
        "</tr></thead><tbody>"
        + "".join(rows) +
        "</tbody></table>"
        "</div>"
    )
    return html


# -----------------------------
# Helpers for SMTP with IPv4-first
# -----------------------------
def _resolve_all(host, port, ipv4_only=False):
    flags = []
    try:
        info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        log_mail(f"DNS resolution failed for {host}:{port} -> {e!r}")
        return []

    if ipv4_only:
        info = [a for a in info if a[0] == socket.AF_INET]

    # Sort IPv4 first to avoid IPv6 route issues
    info_sorted = sorted(info, key=lambda a: 0 if a[0] == socket.AF_INET else 1)
    addrs = []
    for family, socktype, proto, canonname, sockaddr in info_sorted:
        ip = sockaddr[0]
        addrs.append((family, ip))
    return addrs


def _smtp_send_all_addrs(host, port, sender, password, recipients, msg_str, ipv4_only=False):
    addrs = _resolve_all(host, port, ipv4_only=ipv4_only)
    if not addrs:
        log_mail(f"No addresses to try for {host}:{port}")
        return False

    last_err = None
    for i, (family, ip) in enumerate(addrs, start=1):
        fam = 'IPv4' if family == socket.AF_INET else 'IPv6'
        log_mail(f"Trying {host}:{port} -> {ip} ({fam}) [{i}/{len(addrs)}]")
        try:
            import smtplib  # standard library
            with smtplib.SMTP(host=ip, port=port, timeout=20) as server:
                server.ehlo()
                # STARTTLS on 587
                context = ssl.create_default_context()
                server.starttls(context=context)
                server.ehlo()
                server.login(sender, password)
                server.sendmail(sender, recipients, msg_str)
            log_mail(f"Email sent ✔ via {ip}")
            return True
        except Exception as e:
            log_mail(f"Failed via {ip}: {e!r}")
            last_err = e
            time.sleep(1)

    if last_err:
        log_mail(f"All addresses failed; last error: {last_err!r}")
    return False


# -----------------------------
# Optional HTTP email via SendGrid (no external deps required)
# -----------------------------
def _send_via_sendgrid(sender_email, sender_name, recipients, subject, html):
    api_key = os.environ.get("SENDGRID_API_KEY")
    if not api_key:
        return False, "SENDGRID_API_KEY not set"

    payload = {
        "personalizations": [{"to": [{"email": r} for r in recipients]}],
        "from": {"email": sender_email, "name": sender_name or "Eurostar Snap Bot"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html}],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url="https://api.sendgrid.com/v3/mail/send",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = resp.getcode()
            if status >= 300:
                return False, f"SendGrid HTTP {status}"
            return True, None
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            err_body = str(e)
        return False, f"SendGrid HTTPError {e.code}: {err_body}"
    except Exception as e:
        return False, repr(e)


# -----------------------------
# Public email function
# -----------------------------
def send_email(available_entries):
    """Build and send email; never crash the process on failure."""
    if not available_entries:
        log_mail("No entries -> skip email")
        return

    # Env
    sender_email = os.environ.get("EMAIL_SENDER", "").strip()
    sender_name = os.environ.get("EMAIL_FROM_NAME", "Eurostar Snap Bot").strip()
    password = os.environ.get("EMAIL_PASSWORD", "")
    recipients_raw = os.environ.get("EMAIL_RECIPIENT", "")
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_server_alt = os.environ.get("SMTP_SERVER_ALT", "smtp.googlemail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    ipv4_only = os.environ.get("FORCE_IPV4_ONLY", "0") == "1"
    route_label = os.environ.get("ROUTE_LABEL", "")
    subject_prefix = os.environ.get("EMAIL_SUBJECT_PREFIX", "")

    # Recipients parsing
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]

    # HTML
    html = build_email_html(available_entries)
    subject_core = f"New availability detected{(' — ' + route_label) if route_label else ''}"
    subject = f"{subject_prefix}{subject_core}".strip()

    # Message
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender_email)) if sender_email else sender_name
    msg["To"] = ", ".join(recipients)

    # Guard checks
    if not recipients:
        log_mail("[warn] No valid recipients -> skip email")
        return

    # If SendGrid API key exists, try HTTP first (port 443, more stable on PaaS)
    api_key = os.environ.get("SENDGRID_API_KEY")
    if api_key:
        log_mail("SENDGRID_API_KEY detected -> trying HTTPS email first")
        ok, err = _send_via_sendgrid(sender_email or "no-reply@example.com", sender_name, recipients, subject, html)
        if ok:
            log_mail("Email sent via SendGrid ✔")
            return
        else:
            log_mail(f"[warn] SendGrid failed: {err}. Will try SMTP fallback…")

    # SMTP path
    if not sender_email or not password:
        log_mail("[warn] Missing EMAIL_SENDER or EMAIL_PASSWORD for SMTP -> skip SMTP")
        return

    max_global_retries = 2
    for attempt in range(1, max_global_retries + 1):
        log_mail(f"Connecting SMTP {smtp_server}:{smtp_port} (attempt {attempt}/{max_global_retries})")
        ok = _smtp_send_all_addrs(smtp_server, smtp_port, sender_email, password, recipients, msg.as_string(), ipv4_only=ipv4_only)
        if ok:
            return
        log_mail("Trying ALT host…")
        ok = _smtp_send_all_addrs(smtp_server_alt, smtp_port, sender_email, password, recipients, msg.as_string(), ipv4_only=ipv4_only)
        if ok:
            return
        if attempt < max_global_retries:
            log_mail("Global retry in 5s…")
            time.sleep(5)

    log_mail("[err] Email delivery failed after all attempts. Continuing without crash.")


# -----------------------------
# Your scraping/collection logic
# -----------------------------
def collect_availability():
    """
    Placeholder collector.
    Replace this with your actual scraping logic that returns a list of dicts.

    Tips to reduce false negatives:
    - If price is found but time range isn't, keep the offer with band='unknown'
      instead of skipping it, so you still get notified.
    - Normalize dates to YYYY-MM-DD where possible.
    - Attach route/source/url for better email context.
    """
    # Example empty default: integrate your real logic here.
    return []


# -----------------------------
# Main
# -----------------------------
def main():
    log("Starting checker…")
    try:
        entries = collect_availability()
    except Exception as e:
        log(f"[err] collect_availability failed: {e!r}")
        entries = []

    # Send email only if there is something to say
    try:
        send_email(entries)
    except Exception as e:
        # Belt-and-suspenders: never crash overall job on mail
        log_mail(f"[fatal] Unexpected mail exception (suppressed): {e!r}")

    log("Done.")


if __name__ == "__main__":
    main()
