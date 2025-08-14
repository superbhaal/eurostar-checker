import os
import asyncio
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import smtplib
from email.mime.text import MIMEText
import re

# Configuration from environment variables
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT")  # comma-separated emails
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# SNAP URLs
SNAP_PARIS_TO_AMS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8727100&destination=8400058&outbound={date}"
SNAP_AMS_TO_PARIS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8400058&destination=8727100&outbound={date}"

# -------------------- Helpers for time/price parsing --------------------

def _normalize_time_component(value: int) -> str:
    return f"{value:02d}"


def _normalize_time_string(time_str: str) -> str:
    if not time_str:
        return ""
    cleaned = time_str.strip().lower().replace("h", ":")
    m = re.search(r"(\d{1,2}):(\d{2})", cleaned)
    if not m:
        return ""
    hour = int(m.group(1))
    minute = int(m.group(2))
    return f"{_normalize_time_component(hour)}:{_normalize_time_component(minute)}"


def _parse_time_range_from_text(text: str):
    if not text:
        return None
    lowered = text.lower().replace("h", ":")
    # Accept separators '-', '–', '—', 'to', 'à'
    m = re.search(r"(\d{1,2}:\d{2})\s*(?:-|–|—|to|à)\s*(\d{1,2}:\d{2})", lowered)
    if not m:
        # Try to capture without minutes on either side (e.g., 7-12 or 7h-12h)
        m2 = re.search(r"(\d{1,2})(?::?(\d{2}))?\s*(?:-|–|—|to|à)\s*(\d{1,2})(?::?(\d{2}))?", lowered)
        if not m2:
            return None
        sh = int(m2.group(1))
        sm = int(m2.group(2)) if m2.group(2) else 0
        eh = int(m2.group(3))
        em = int(m2.group(4)) if m2.group(4) else 0
        return f"{_normalize_time_component(sh)}:{_normalize_time_component(sm)}", f"{_normalize_time_component(eh)}:{_normalize_time_component(em)}"
    start = _normalize_time_string(m.group(1))
    end = _normalize_time_string(m.group(2))
    if not start or not end:
        return None
    return start, end


def _infer_band(label_text: str, time_range):
    text = (label_text or "").lower()
    if any(k in text for k in ["morning", "matin"]):
        return "morning"
    if any(k in text for k in ["afternoon", "apres", "après"]):
        return "afternoon"
    if time_range:
        try:
            start_hour = int(time_range[0].split(":")[0])
            # Morning before 14:00, otherwise afternoon
            return "morning" if start_hour < 14 else "afternoon"
        except Exception:
            return None
    return None


def _price_to_float(price_text: str) -> float:
    if not price_text:
        return float("inf")
    # Remove thin spaces and convert comma decimal to dot
    normalized = price_text.replace("\u202f", "").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", normalized)
    if not m:
        return float("inf")
    try:
        return float(m.group(1))
    except Exception:
        return float("inf")


def _merge_time_ranges(time_ranges):
    # time_ranges: list of (start, end)
    if not time_ranges:
        return None
    def to_minutes(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    starts = sorted(time_ranges, key=lambda r: to_minutes(r[0]))
    ends = sorted(time_ranges, key=lambda r: to_minutes(r[1]), reverse=True)
    return starts[0][0], ends[0][1]

async def check_snap(playwright, route_name, base_url):
    browser = await playwright.chromium.launch()
    page = await browser.new_page()
    results = []

    for i in range(1, 9):
        date = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
        url = base_url.format(date=date)
        print(f"[Snap] Checking {route_name}: {url}")

        try:
            await page.goto(url, timeout=60000)
            await page.wait_for_timeout(5000)

            # Try multiple selectors to find price elements
            price_blocks = await page.query_selector_all("div[data-testid$='-price'], [data-testid*='price'], .price, [class*='price']")
            
            print(f"[DEBUG] Found {len(price_blocks)} price blocks for {date}")
            
            if not price_blocks:
                # Fallback: try to find any text that looks like a price
                all_text = await page.inner_text("body")
                price_pattern = re.findall(r'€\s*\d+[\.,]?\d*|\d+[\.,]?\d*\s*€', all_text)
                if price_pattern:
                    print(f"[DEBUG] Found prices in text: {price_pattern[:3]}...")
                    # Create dummy blocks for debugging
                    price_blocks = [None] * len(price_pattern)

            offers = []
            for block in price_blocks:
                try:
                    if block:
                        price_text = (await block.inner_text()).strip()
                    else:
                        # Use dummy price for debugging
                        price_text = "€XX (debug)"
                except Exception as e:
                    print(f"[DEBUG] Error getting price text: {e}")
                    continue

                if block:
                    try:
                        info = await block.evaluate("""
                            (el) => {
                                function findContainer(node) {
                                    let cur = node;
                                    for (let i = 0; i < 6 && cur; i++) {
                                        const hasPrice = cur.querySelector("[data-testid$='-price'], [data-testid*='price']");
                                        const hasTime = cur.querySelector("[data-testid*='time'], time, [class*='time']");
                                        if (hasPrice && (hasTime || i > 0)) return cur;
                                        cur = cur.parentElement;
                                    }
                                    return node;
                                }
                                const container = findContainer(el);
                                const timeEl = container.querySelector("[data-testid*='time'], time, [class*='time']");
                                const labelEl = container.querySelector("[data-testid*='band'], [data-testid*='period'], [class*='morning'], [class*='afternoon']");
                                return {
                                    containerText: container && container.innerText ? container.innerText : '',
                                    timeText: timeEl && timeEl.innerText ? timeEl.innerText : '',
                                    labelText: labelEl && labelEl.innerText ? labelEl.innerText : ''
                                };
                            }
                        """)
                    except Exception as e:
                        print(f"[DEBUG] Error in evaluate: {e}")
                        info = {}
                else:
                    info = {}

                container_text = info.get("containerText", "") if isinstance(info, dict) else ""
                time_text = info.get("timeText", "") if isinstance(info, dict) else ""
                label_text = info.get("labelText", "") if isinstance(info, dict) else ""

                print(f"[DEBUG] Price: {price_text}, Time: {time_text}, Label: {label_text}")
                
                time_range = _parse_time_range_from_text(time_text) or _parse_time_range_from_text(container_text)
                band = _infer_band(label_text, time_range)
                
                # If no band detected, try to infer from time or assign default
                if not band:
                    if time_range:
                        band = _infer_band("", time_range)
                    if not band:
                        band = "morning"  # Default fallback
                
                print(f"[DEBUG] Assigned band: {band}")
                
                offers.append({
                    "band": band,
                    "price_text": price_text,
                    "time_range": time_range
                })

            print(f"[DEBUG] Total offers found: {len(offers)}")
            
            if offers:
                entry = {"route": route_name, "date": date, "url": url, "morning": None, "afternoon": None}
                for band in ["morning", "afternoon"]:
                    band_offers = [o for o in offers if o["band"] == band]
                    if band_offers:
                        # Select lowest price and merge time ranges
                        best_price_offer = min(band_offers, key=lambda o: _price_to_float(o["price_text"]))
                        merged_range = _merge_time_ranges([o["time_range"] for o in band_offers if o["time_range"]]) if any(o["time_range"] for o in band_offers) else None
                        entry[band] = {
                            "price_text": best_price_offer["price_text"],
                            "time_range": merged_range,
                            "url": url,
                        }
                if entry["morning"] or entry["afternoon"]:
                    results.append(entry)
                    print(f"[DEBUG] Added entry for {date}: morning={entry['morning'] is not None}, afternoon={entry['afternoon'] is not None}")
            else:
                print(f"[DEBUG] No offers found for {date}")

        except Exception as e:
            print(f"Erreur SNAP pour {route_name} le {date} : {e}")

    await browser.close()
    return results

def send_email(available_entries):
    if not available_entries:
        return

    def build_table(rows):
        # rows: list of dict with keys date, morning, afternoon
        parts = []
        parts.append('<table style="border-collapse:collapse;width:100%;max-width:720px;font-family:Arial,Helvetica,sans-serif">')
        # header
        th_style = 'style="border:1px solid #ddd;padding:8px;text-align:left;background:#f7f7f7"'
        td_style = 'style="border:1px solid #ddd;padding:8px;text-align:left"'
        parts.append(f"<tr><th {th_style}>Date</th><th {th_style}>Morning</th><th {th_style}>Afternoon</th></tr>")
        for r in rows:
            def cell_content(slot):
                if not slot:
                    return "—<br/><small>time not specified</small>"
                price_html = f'<a href="{slot["url"]}">{slot["price_text"]}</a>'
                if slot.get("time_range"):
                    start, end = slot["time_range"]
                    return f"{price_html}<br/><small>between {start} and {end}</small>"
                return f"{price_html}<br/><small>time not specified</small>"
            parts.append(
                f"<tr>"
                f"<td {td_style}>{r['date']}</td>"
                f"<td {td_style}>{cell_content(r.get('morning'))}</td>"
                f"<td {td_style}>{cell_content(r.get('afternoon'))}</td>"
                f"</tr>"
            )
        parts.append("</table>")
        return "".join(parts)

    header = (
        "<div style=\"font-family:Arial,Helvetica,sans-serif\">"
        "<h2>🤖 The bot for cheap tickets between Amsterdam and Paris 🤖</h2>"
        "<p>🚄 Eurostar Snap availability</p>"
        "</div>"
    )

    sections = []
    for route in ["Paris → Amsterdam", "Amsterdam → Paris"]:
        route_entries = [e for e in available_entries if e["route"] == route]
        if not route_entries:
            continue
        # sort by date
        route_entries_sorted = sorted(route_entries, key=lambda e: e["date"])
        table_html = build_table(route_entries_sorted)
        sections.append(
            f"<h3 style=\"font-family:Arial,Helvetica,sans-serif\">{route}</h3>" + table_html
        )

    message = header + "".join(sections)

    msg = MIMEText(message, "html", "utf-8")
    msg["Subject"] = "🤖 The bot for cheap tickets between Amsterdam and Paris 🤖"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECIPIENT

    recipients = EMAIL_RECIPIENT.split(",")
    print(f"[Recipients {recipients}")
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
            print(f"ALL_AVAILABLE: {all_available}")
            send_email(all_available)

    asyncio.run(run())

if __name__ == "__main__":
    main()
