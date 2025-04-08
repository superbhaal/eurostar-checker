import os
import asyncio
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import smtplib
from email.mime.text import MIMEText
from bs4 import BeautifulSoup

# Configuration email depuis variables d'environnement
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# URLs de base pour SNAP Eurostar
PARIS_TO_AMS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8727100&destination=8400058&outbound={date}"
AMS_TO_PARIS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8400058&destination=8727100&outbound={date}"

async def check_availability(playwright, route_name, base_url):
    browser = await playwright.chromium.launch()
    page = await browser.new_page()
    available = []

    for i in range(1, 9):  # J+1 à J+8
        date = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
        url = base_url.format(date=date)
        print(f"Checking {route_name}: {url}")

        try:
            await page.goto(url, timeout=60000)
            await page.wait_for_timeout(5000)

            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")
            price_blocks = soup.find_all("div", attrs={"data-testid": lambda x: x and "-price" in x})

            if price_blocks:
                prices = [block.get_text(strip=True) for block in price_blocks]
                price_summary = ", ".join(prices)
                print(f"✅ Disponibilité trouvée pour {route_name} le {date} ({price_summary})")
                available.append((route_name, date, url, price_summary))
            else:
                print(f"❌ Aucune disponibilité pour {route_name} le {date}")

        except Exception as e:
            print(f"Erreur pour {route_name} le {date} : {e}")

    await browser.close()
    return available

def send_email(available_trips):
    if not available_trips:
        return

    message = "🚄 Disponibilités Eurostar détectées :\n\n"
    for route, date, url, price in available_trips:
        message += f"- {route} le {date} : {price} → {url}\n"

    msg = MIMEText(message)
    msg["Subject"] = "📬 Trains Eurostar disponibles"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECIPIENT

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())

def main():
    async def run():
        async with async_playwright() as playwright:
            paris_ams = await check_availability(playwright, "Paris → Amsterdam", PARIS_TO_AMS)
            ams_paris = await check_availability(playwright, "Amsterdam → Paris", AMS_TO_PARIS)
            all_available = paris_ams + ams_paris
            send_email(all_available)

    asyncio.run(run())

if __name__ == "__main__":
    main()
