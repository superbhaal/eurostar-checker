import os
import asyncio
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import smtplib
from email.mime.text import MIMEText

# Configuration from environment variables
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT")  # comma-separated emails
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# SNAP URLs
SNAP_PARIS_TO_AMS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8727100&destination=8400058&outbound={date}"
SNAP_AMS_TO_PARIS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8400058&destination=8727100&outbound={date}"

async def check_snap(playwright, route_name, base_url):
    browser = await playwright.chromium.launch()
    page = await browser.new_page()
    available = []

    for i in range(1, 9):
        date = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
        url = base_url.format(date=date)
        print(f"[Snap] Checking {route_name}: {url}")

        try:
            await page.goto(url, timeout=60000)
            await page.wait_for_timeout(5000)
            price_blocks = await page.query_selector_all("div[data-testid$='-price']")

            if price_blocks:
                prices = [await block.inner_text() for block in price_blocks]
                price_summary = ", ".join(prices)
                available.append((route_name, date, url, f"{price_summary} (Eurostar Snap)"))

        except Exception as e:
            print(f"Erreur SNAP pour {route_name} le {date} : {e}")

    await browser.close()
    return available

def send_email(available_trips):
    if not available_trips:
        return

    message = "🚄 Eurostar Snap Availability Detected:\n\n"

    for route in ["Paris → Amsterdam", "Amsterdam → Paris"]:
        trips = [t for t in available_trips if t[0] == route]
        if trips:
            message += f"### {route} ###\n"
            for _, date, url, price in trips:
                message += f"- {date}: {price} → {url}\n"
            message += "\n"

    msg = MIMEText(message)
    msg["Subject"] = "🤖 The Bot to Reunite Lovers & Friends - Eurostar Snap Availability ❤️"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECIPIENT

    recipients = EMAIL_RECIPIENT.split(",")
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, recipients, msg.as_string())

def main():
    async def run():
        async with async_playwright() as playwright:
            snap_1 = await check_snap(playwright, "Paris → Amsterdam", SNAP_PARIS_TO_AMS)
            snap_2 = await check_snap(playwright, "Amsterdam → Paris", SNAP_AMS_TO_PARIS)
            all_available = snap_1 + snap_2
            send_email(all_available)

    asyncio.run(run())

if __name__ == "__main__":
    main()
