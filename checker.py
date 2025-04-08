import os
import asyncio
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import smtplib
from email.mime.text import MIMEText

# Configuration email depuis variables d'environnement
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# URL de base pour SNAP Eurostar
BASE_URL = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8727100&destination=8400058&outbound={date}"

async def check_availability(playwright):
    browser = await playwright.chromium.launch()
    page = await browser.new_page()
    available = []

    for i in range(1, 9):  # J+1 à J+8
        date = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
        url = BASE_URL.format(date=date)
        print(f"Checking: {url}")

        try:
            await page.goto(url, timeout=60000)
            await page.wait_for_timeout(5000)

            content = await page.content()
            print(content)
            if "Désolés, cet itinéraire est actuellement complet" not in content:
                available.append((date, url))

        except Exception as e:
            print(f"Erreur pour {date} : {e}")

    await browser.close()
    return available

def send_email(available_dates):
    message = "🚄 Trains disponibles trouvés aux dates suivantes :\n\n"
    for date, url in available_dates:
        message += f"- {date} → {url}\n"

    msg = MIMEText(message)
    msg["Subject"] = "📬 Disponibilités Eurostar détectées"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECIPIENT

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())

def main():
    async def run():
        async with async_playwright() as playwright:
            available_dates = await check_availability(playwright)
            if available_dates:
                send_email(available_dates)
            else:
                print("Aucune disponibilité trouvée.")
    asyncio.run(run())

if __name__ == "__main__":
    main()
