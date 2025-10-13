
import os
import asyncio
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import re
import json
import urllib.request

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT")
BREVO_API_KEY = os.getenv("BREVO_API_KEY")

if not EMAIL_SENDER or not EMAIL_RECIPIENT:
    raise RuntimeError("Missing env vars: set EMAIL_SENDER and EMAIL_RECIPIENT")
if not BREVO_API_KEY:
    raise RuntimeError("Missing env var BREVO_API_KEY (Brevo/Sendinblue API key)")

SNAP_PARIS_TO_AMS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8727100&destination=8400058&outbound={date}"
SNAP_AMS_TO_PARIS = "https://snap.eurostar.com/fr-fr/search?adult=1&origin=8400058&destination=8727100&outbound={date}"

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
    lowered = text.strip().lower()
    m = re.search(r'départ\s+entre\s+(\d{1,2}):(\d{2})\s+et\s+(\d{1,2}):(\d{2})', lowered)
    if m:
        sh, sm, eh, em = map(int, m.groups())
        return f"{_normalize_time_component(sh)}:{_normalize_time_component(sm)}", f"{_normalize_time_component(eh)}:{_normalize_time_component(em)}"
    m = re.search(r'départ\s+entre\s+(\d{1,2})h(\d{2})\s+et\s+(\d{1,2})h(\d{2})', lowered)
    if m:
        sh, sm, eh, em = map(int, m.groups())
        return f"{_normalize_time_component(sh)}:{_normalize_time_component(sm)}", f"{_normalize_time_component(eh)}:{_normalize_time_component(em)}"
    m = re.search(r'departure\s+between\s+(\d{1,2}):(\d{2})\s+and\s+(\d{1,2}):(\d{2})', lowered)
    if m:
        sh, sm, eh, em = map(int, m.groups())
        return f"{_normalize_time_component(sh)}:{_normalize_time_component(sm)}", f"{_normalize_time_component(eh)}:{_normalize_time_component(em)}"
    m = re.search(r"(\d{1,2}:\d{2})\s*(?:-|–|—|to|à)\s*(\d{1,2}:\d{2})", lowered)
    if m:
        start = _normalize_time_string(m.group(1)); end = _normalize_time_string(m.group(2))
        if start and end:
            return start, end
    m2 = re.search(r"(\d{1,2})(?::?(\d{2}))?\s*(?:-|–|—|to|à)\s*(\d{1,2})(?::?(\d{2}))?", lowered)
    if m2:
        sh = int(m2.group(1)); sm = int(m2.group(2) or 0); eh = int(m2.group(3)); em = int(m2.group(4) or 0)
        return f"{_normalize_time_component(sh)}:{_normalize_time_component(sm)}", f"{_normalize_time_component(eh)}:{_normalize_time_component(em)}"
    return None

def _infer_band(label_text: str, time_range):
    text = (label_text or "").lower()
    if any(k in text for k in ["morning", "matin"]):
        return "morning"
    if any(k in text for k in ["afternoon", "apres", "après"]):
        return "afternoon"
    if time_range:
        try:
            start_hour = int(time_range[0].split(":")[0])
            return "morning" if start_hour < 14 else "afternoon"
        except Exception:
            return None
    return None

def _price_to_float(price_text: str) -> float:
    if not price_text:
        return float("inf")
    normalized = price_text.replace("\u202f", "").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", normalized)
    if not m:
        return float("inf")
    try:
        return float(m.group(1))
    except Exception:
        return float("inf")

def _merge_time_ranges(time_ranges):
    if not time_ranges:
        return None
    def to_minutes(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    starts = sorted(time_ranges, key=lambda r: to_minutes(r[0]))
    ends = sorted(time_ranges, key=lambda r: to_minutes(r[1]), reverse=True)
    return starts[0][0], ends[0][1]

def _format_date_for_display(date_str: str) -> str:
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        day_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
        day_name = day_names[date_obj.weekday()]
        day = date_obj.day
        suffix = "th" if 4 <= day <= 20 or 24 <= day <= 30 else ["st","nd","rd"][day % 10 - 1]
        month_names = ["January","February","March","April","May","June","July","August","September","October","November","December"]
        month_name = month_names[date_obj.month-1]
        return f"{day_name} {day}{suffix} {month_name} {date_obj.year}"
    except Exception:
        return date_str

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

            price_blocks = await page.query_selector_all("div[data-testid$='-price'], [data-testid*='price'], .price, [class*='price']")
            print(f"[DEBUG] Found {len(price_blocks)} price blocks for {date}")
            if not price_blocks:
                all_text = await page.inner_text("body")
                price_pattern = re.findall(r'€\s*\d+[\.,]?\d*|\d+[\.,]?\d*\s*€', all_text)
                if price_pattern:
                    print(f"[DEBUG] Found prices in text: {price_pattern[:3]}...")
                    price_blocks = [None] * len(price_pattern)

            offers = []
            for block in price_blocks:
                try:
                    price_text = (await block.inner_text()).strip() if block else "€XX (debug)"
                except Exception:
                    continue

                if block:
                    try:
                        info = await block.evaluate("""
                            (el) => {
                                function findContainer(node){
                                    let cur = node;
                                    for (let i=0;i<8 && cur;i++){
                                        const hasPrice = cur.querySelector("[data-testid$='-price'], [data-testid*='price'], .price, [class*='price']");
                                        const hasTime  = cur.querySelector("[data-testid*='time'], time, [class*='time'], [class*='hour'], [class*='departure'], [class*='schedule']");
                                        if (hasPrice && (hasTime || i>0)) return cur;
                                        cur = cur.parentElement;
                                    }
                                    return node;
                                }
                                function findTimeElements(container){
                                    const timeSelectors = ["[data-testid*='time']","time","[class*='time']","[class*='hour']","[class*='departure']","[class*='schedule']"];
                                    let timeElements = [];
                                    timeSelectors.forEach(sel=>{
                                        container.querySelectorAll(sel).forEach(el=>{
                                            if (el.innerText && el.innerText.trim()) timeElements.push(el.innerText.trim());
                                        });
                                    });
                                    const containerText = container.innerText || "";
                                    const m = containerText.match(/(\\d{1,2}:\\d{2})\\s*(?:-|–|—|to|à)\\s*(\\d{1,2}:\\d{2})/gi);
                                    if (m) timeElements.push(...m);
                                    return timeElements;
                                }
                                const container = findContainer(el);
                                const timeElements = findTimeElements(container);
                                const labelEl = container.querySelector("[data-testid*='band'], [data-testid*='period'], [class*='morning'], [class*='afternoon'], [class*='matin'], [class*='apres']");
                                return {
                                    containerText: container && container.innerText ? container.innerText : '',
                                    timeElements: timeElements,
                                    labelText: labelEl && labelEl.innerText ? labelEl.innerText : ''
                                };
                            }
                        """)
                    except Exception:
                        info = {}
                else:
                    info = {}

                container_text = info.get("containerText","") if isinstance(info, dict) else ""
                time_elements = info.get("timeElements",[]) if isinstance(info, dict) else []
                label_text = info.get("labelText","") if isinstance(info, dict) else ""

                time_range = None
                for time_text in time_elements:
                    time_range = _parse_time_range_from_text(time_text)
                    if time_range: break
                if not time_range:
                    time_range = _parse_time_range_from_text(container_text)

                band = _infer_band(label_text, time_range) or ("morning" if (time_range and int(time_range[0].split(":")[0]) < 14) else "afternoon")

                if price_text != "€XX (debug)" and time_range:
                    offers.append({"band": band, "price_text": price_text, "time_range": time_range})

            if offers:
                entry = {"route": route_name, "date": date, "url": url, "morning": None, "afternoon": None}
                for band in ["morning","afternoon"]:
                    band_offers = [o for o in offers if o["band"] == band]
                    if band_offers:
                        best = min(band_offers, key=lambda o: _price_to_float(o["price_text"])) 
                        merged = _merge_time_ranges([o["time_range"] for o in band_offers if o["time_range"]])
                        entry[band] = {"price_text": best["price_text"], "time_range": merged, "url": url}
                if entry["morning"] or entry["afternoon"]:
                    results.append(entry)
            else:
                results.append({"route": route_name, "date": date, "url": url, "morning": None, "afternoon": None})
        except Exception as e:
            print(f"Erreur SNAP pour {route_name} le {date} : {e}")

    await browser.close()
    return results

def send_email_brevo(available_entries):
    def build_table(rows):
        parts = []
        th_style = 'style="border:1px solid #ddd;padding:8px;text-align:left;background:#f7f7f7"'
        td_style = 'style="border:1px solid #ddd;padding:8px;text-align:left"'
        parts.append('<table style="border-collapse:collapse;width:100%;max-width:720px;font-family:Arial,Helvetica,sans-serif">')
        parts.append(f"<tr><th {th_style}>Date</th><th {th_style}>Morning</th><th {th_style}>Afternoon</th></tr>")
        for r in rows:
            def cell(slot):
                if not slot: return "—<br/><small>no availability for now</small>"
                price_html = f'<a href="{slot["url"]}">{slot["price_text"]}</a>'
                if slot.get("time_range"):
                    start,end = slot["time_range"]; return f"{price_html}<br/><small>between {start} and {end}</small>"
                return f"{price_html}<br/><small>no availability for now</small>"
            parts.append(f"<tr><td {td_style}>{_format_date_for_display(r['date'])}</td><td {td_style}>{cell(r.get('morning'))}</td><td {td_style}>{cell(r.get('afternoon'))}</td></tr>")
        parts.append("</table>")
        return ''.join(parts)

    header = "<div style=\"font-family:Arial,Helvetica,sans-serif\"><h2>Eurostar Snap availability</h2></div>"
    sections = []
    for route in ["Paris → Amsterdam","Amsterdam → Paris"]:
        route_entries = [e for e in available_entries if e["route"] == route]
        if not route_entries: 
            continue
        route_entries_sorted = sorted(route_entries, key=lambda e: e["date"])  # <-- fixed bracket here
        table_html = build_table(route_entries_sorted)
        sections.append(f"<h3 style=\"font-family:Arial,Helvetica,sans-serif\">{route}</h3>" + table_html)
    html = header + "".join(sections) if sections else header + "<p>No availability for selected dates.</p>"

    recipients = [email.strip() for email in EMAIL_RECIPIENT.split(",") if email.strip()]
    to_list = [{"email": r} for r in recipients]
    payload = {
        "sender": {"email": EMAIL_SENDER, "name": "Eurostar Snap"},
        "to": to_list,
        "subject": "Eurostar Snap — disponibilité détectée" if any(e.get("morning") or e.get("afternoon") for e in available_entries) else "Eurostar Snap — rapport (aucune dispo)",
        "htmlContent": html
    }

    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "api-key": BREVO_API_KEY,
            "Content-Type": "application/json",
            "accept": "application/json",
        },
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status not in (200, 201, 202):
            raise RuntimeError(f"Brevo HTTP error: {resp.status}")

def main():
    async def run():
        async with async_playwright() as playwright:
            snap_1 = await check_snap(playwright, "Paris → Amsterdam", SNAP_PARIS_TO_AMS)
            snap_2 = await check_snap(playwright, "Amsterdam → Paris", SNAP_AMS_TO_PARIS)
            all_available = snap_1 + snap_2
            print(f"ALL_AVAILABLE: {all_available}")
            send_email_brevo(all_available)

    asyncio.run(run())

if __name__ == "__main__":
    main()
