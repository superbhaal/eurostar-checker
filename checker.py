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

# URLs de base
SNAP_PARIS_TO_AMS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8727100&destination=8400058&outbound={date}"
SNAP_AMS_TO_PARIS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8400058&destination=8727100&outbound={date}"
TRAINLINE_PARIS_TO_AMS = "https://www.thetrainline.com/book/results?origin=urn%3Atrainline%3Ageneric%3Aloc%3A4916&destination=urn%3Atrainline%3Ageneric%3Aloc%3A8657&outwardDate={date}T14%3A00%3A00&outwardDateType=departAfter&journeySearchType=single&passengers%5B%5D=1992-04-20%7Cpid-0&directSearch=true&transportModes%5B%5D=mixed&lang=fr"
TRAINLINE_AMS_TO_PARIS = "https://www.thetrainline.com/book/results?origin=urn%3Atrainline%3Ageneric%3Aloc%3A8657&destination=urn%3Atrainline%3Ageneric%3Aloc%3A4916&outwardDate={date}T14%3A00%3A00&outwardDateType=departAfter&journeySearchType=single&passengers%5B%5D=1992-04-20%7Cpid-0&directSearch=true&transportModes%5B%5D=mixed&lang=fr"

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

async def check_trainline(playwright, route_name, base_url):
    browser = await playwright.chromium.launch()
    page = await browser.new_page()
    available = []

    for i in range(1, 9):
        date = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
        url = base_url.format(date=date)
        print(f"[Trainline] Checking {route_name}: {url}")

        try:
            await page.goto(url, timeout=60000)
            await page.wait_for_timeout(7000)

            trips = await page.query_selector_all("[data-test='OutwardJourneyOption']")
            print(f"Trainline: {len(trips)} trajets trouvés")

            for trip in trips:
                price = await trip.query_selector("[data-test='JourneyPrice']")
                if price:
                    price_text = await price.inner_text()
                    if price_text.strip():
                        available.append((route_name, date, url, f"{price_text} (Trainline)"))

        except Exception as e:
            print(f"Erreur TRAINLINE pour {route_name} le {date} : {e}")

    await browser.close()
    return available

def send_email(available_trips):
    if not available_trips:
        return

    message = "🚄 Disponibilités train détectées :\n\n"
    for route, date, url, price in available_trips:
        message += f"- {route} le {date} : {price} → {url}\n"

    msg = MIMEText(message)
    msg["Subject"] = "📬 Trains disponibles"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECIPIENT

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())

def main():
    async def run():
        async with async_playwright() as playwright:
            snap_1 = await check_snap(playwright, "Paris → Amsterdam", SNAP_PARIS_TO_AMS)
            snap_2 = await check_snap(playwright, "Amsterdam → Paris", SNAP_AMS_TO_PARIS)
            trainline_1 = await check_trainline(playwright, "Paris → Amsterdam", TRAINLINE_PARIS_TO_AMS)
            trainline_2 = await check_trainline(playwright, "Amsterdam → Paris", TRAINLINE_AMS_TO_PARIS)

            all_available = snap_1 + snap_2 + trainline_1 + trainline_2
            send_email(all_available)

    asyncio.run(run())

if __name__ == "__main__":
    main()