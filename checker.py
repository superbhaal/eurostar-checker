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

# URLs de base
SNAP_PARIS_TO_AMS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8727100&destination=8400058&outbound={date}"
SNAP_AMS_TO_PARIS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8400058&destination=8727100&outbound={date}"
EUROSTAR_PARIS_TO_AMS = "https://www.eurostar.com/search/fr-fr?origin=8727100&destination=8400058&adult=1&child=0&infant=0&youth=0&senior=0&direction=outbound&outbound={date}"
EUROSTAR_AMS_TO_PARIS = "https://www.eurostar.com/search/fr-fr?adult=1&origin=8400058&destination=8727100&outbound={date}"

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
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")
            price_blocks = soup.find_all("div", attrs={"data-testid": lambda x: x and "-price" in x})

            if price_blocks:
                prices = [block.get_text(strip=True) for block in price_blocks]
                price_summary = ", ".join(prices)
                available.append((route_name, date, url, f"{price_summary} (Eurostar Snap)"))

        except Exception as e:
            print(f"Erreur SNAP pour {route_name} le {date} : {e}")

    await browser.close()
    return available

async def check_eurostar(playwright, route_name, base_url):
    browser = await playwright.chromium.launch()
    page = await browser.new_page()
    available = []

    for i in range(1, 9):
        date = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
        url = base_url.format(date=date)
        print(f"[Main Site] Checking {route_name}: {url}")

        try:
            await page.goto(url, timeout=60000)

            # Fermer la popup si présente
            try:
                await page.wait_for_selector('button[aria-label="Fermer"]', timeout=3000)
                await page.click('button[aria-label="Fermer"]')
                print("Popup fermée")
            except:
                print("Pas de popup à fermer")

            await page.wait_for_timeout(5000)
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")

            rows = soup.select(".fare-table__row")
            for row in rows:
                time_block = row.select_one(".fare-table__departure")
                if not time_block:
                    continue
                for cls, label in zip(["standard", "plus", "premier"], ["Eurostar Standard", "Eurostar Plus", "Eurostar Premier"]):
                    cell = row.select_one(f".fare-table__cell--{cls}")
                    if cell and "Non disponible" not in cell.get_text():
                        price = cell.get_text(strip=True).split("\n")[0]
                        available.append((route_name, date, url, f"{price} ({label})"))

        except Exception as e:
            print(f"Erreur EUROSTAR.COM pour {route_name} le {date} : {e}")

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
            snap_1 = await check_snap(playwright, "Paris → Amsterdam", SNAP_PARIS_TO_AMS)
            snap_2 = await check_snap(playwright, "Amsterdam → Paris", SNAP_AMS_TO_PARIS)
            eurostar_1 = await check_eurostar(playwright, "Paris → Amsterdam", EUROSTAR_PARIS_TO_AMS)
            eurostar_2 = await check_eurostar(playwright, "Amsterdam → Paris", EUROSTAR_AMS_TO_PARIS)

            all_available = snap_1 + snap_2 + eurostar_1 + eurostar_2
            send_email(all_available)

    asyncio.run(run())

if __name__ == "__main__":
    main()
